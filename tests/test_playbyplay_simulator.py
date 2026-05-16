from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.schema import Game, Prediction, SandboxPredictionV4
from app.services.playbyplay_simulator import (
    backtest_play_by_play_weights,
    compare_sim_to_actual,
    fetch_actual_play_by_play,
    simulate_play_by_play,
)


class PlayByPlaySimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        testing_session_local = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = testing_session_local()
        game = Game(
            game_id=101,
            game_date=date(2026, 5, 16),
            season=2026,
            away_team="Away Club",
            home_team="Home Club",
            away_team_id=1,
            home_team_id=2,
            venue="Test Park",
            status="Preview",
            start_time=datetime(2026, 5, 16, 18, 0, tzinfo=timezone.utc),
            away_probable_pitcher="Away Starter",
            home_probable_pitcher="Home Starter",
        )
        prediction = Prediction(
            game_id=101,
            model_version="v-test",
            away_win_pct=0.46,
            home_win_pct=0.54,
            projected_away_score=3.9,
            projected_home_score=4.7,
            projected_total=8.6,
            confidence_score=8.0,
            recommended_side="HOME",
            is_active=True,
        )
        sandbox = SandboxPredictionV4(
            game_id=101,
            game_date=date(2026, 5, 16),
            season=2026,
            away_team="Away Club",
            home_team="Home Club",
            f5_projected_total=4.2,
            full_game_projected_total=8.8,
            umpire_name="Tight Zone",
            umpire_run_impact=0.15,
            home_bullpen_strength=0.72,
            away_bullpen_strength=0.44,
            bullpen_convergence=False,
            wind_factor=0.2,
        )
        self.db.add_all([game, prediction, sandbox])
        final_game = Game(
            game_id=102,
            game_date=date(2026, 5, 15),
            season=2026,
            away_team="Away Final",
            home_team="Home Final",
            away_team_id=3,
            home_team_id=4,
            venue="Final Park",
            status="Final",
            start_time=datetime(2026, 5, 15, 18, 0, tzinfo=timezone.utc),
            final_away_score=5,
            final_home_score=4,
        )
        final_prediction = Prediction(
            game_id=102,
            model_version="v-test",
            away_win_pct=0.48,
            home_win_pct=0.52,
            projected_away_score=4.1,
            projected_home_score=4.2,
            projected_total=8.3,
            confidence_score=7.0,
            recommended_side="HOME",
            is_active=False,
        )
        self.db.add_all([final_game, final_prediction])
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    @patch("app.services.playbyplay_simulator.run_v4_sandbox", return_value={"status": "ok"})
    def test_simulate_play_by_play_is_deterministic_and_visual_ready(self, sandbox_mock: Mock) -> None:
        first = simulate_play_by_play(self.db, 101)
        second = simulate_play_by_play(self.db, 101)

        self.assertEqual(first["status"], "ok")
        self.assertEqual(sandbox_mock.call_count, 2)
        self.assertEqual(first["model_version"], "v0.6-pbp-shadow")
        self.assertEqual(first["events"], second["events"])
        self.assertGreater(len(first["events"]), 40)
        self.assertIn("score", first["events"][0])
        self.assertIn("bases_after", first["events"][0])
        self.assertGreaterEqual(len(first["highlights"]), 1)
        self.assertIn("projection_drift", first)
        self.assertTrue(any("is_model_miss_clue" in event for event in first["events"]))
        self.assertTrue(first["context"]["uses_sandbox_signals"])
        self.assertEqual(first["context"]["sandbox_refresh"]["ok"], True)
        self.assertEqual(first["context"]["umpire"]["name"], "Tight Zone")
        self.assertEqual(first["context"]["bullpen"]["away_strength"], 0.44)

    @patch("app.services.playbyplay_simulator.requests.get")
    def test_fetch_actual_play_by_play_summarizes_mlb_feed(self, get_mock: Mock) -> None:
        get_mock.return_value.status_code = 200
        get_mock.return_value.json.return_value = {
            "gameData": {
                "teams": {
                    "away": {"name": "Away Club"},
                    "home": {"name": "Home Club"},
                }
            },
            "liveData": {
                "plays": {
                    "allPlays": [
                        {
                            "about": {"inning": 1, "halfInning": "top"},
                            "result": {"eventType": "home_run", "event": "Home Run", "rbi": 2, "description": "Two-run homer"},
                            "matchup": {"batter": {"fullName": "Batter A"}, "pitcher": {"fullName": "Pitcher H"}},
                        },
                        {
                            "about": {"inning": 1, "halfInning": "bottom"},
                            "result": {"eventType": "strikeout", "event": "Strikeout", "rbi": 0, "description": "Strikeout"},
                            "matchup": {"batter": {"fullName": "Batter H"}, "pitcher": {"fullName": "Pitcher A"}},
                        },
                    ]
                }
            },
        }

        actual = fetch_actual_play_by_play(101)

        self.assertEqual(actual["status"], "ok")
        self.assertEqual(actual["summary"]["runs"], 2)
        self.assertEqual(actual["summary"]["home_runs"], 1)
        self.assertEqual(actual["summary"]["strikeouts"], 1)
        self.assertEqual(len(actual["highlights"]), 1)

    @patch("app.services.playbyplay_simulator.fetch_actual_play_by_play")
    @patch("app.services.playbyplay_simulator.run_v4_sandbox", return_value={"status": "ok"})
    def test_compare_sim_to_actual_reports_not_ready_without_actual_events(
        self,
        sandbox_mock: Mock,
        actual_mock: Mock,
    ) -> None:
        actual_mock.return_value = {"status": "ok", "game_id": 101, "events": [], "summary": {}}

        result = compare_sim_to_actual(self.db, 101)

        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(result["game_id"], 101)
        sandbox_mock.assert_called_once_with(101, self.db)

    @patch("app.services.playbyplay_simulator.fetch_actual_play_by_play")
    def test_backtest_play_by_play_weights_returns_recommended_multipliers(self, actual_mock: Mock) -> None:
        actual_mock.return_value = {
            "status": "ok",
            "events": [
                {"outcome": "single", "label": "Single", "runs": 1, "inning": 1},
                {"outcome": "double", "label": "Double", "runs": 2, "inning": 1},
                {"outcome": "walk", "label": "Walk", "runs": 0, "inning": 2},
                {"outcome": "strikeout", "label": "Strikeout", "runs": 0, "inning": 2},
                {"outcome": "field_out", "label": "Field Out", "runs": 0, "inning": 3},
            ],
        }

        result = backtest_play_by_play_weights(self.db, season=2026, limit=10)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["games_processed"], 1)
        self.assertIn("overall", result["calibration"])
        self.assertIn("single", result["recommended_overall_multipliers"])


if __name__ == "__main__":
    unittest.main()
