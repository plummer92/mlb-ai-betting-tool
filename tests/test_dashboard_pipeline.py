from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.schema import EdgeResult, Game, GameOdds, Prediction, SnapshotType
from app.routes.edges import get_today_edges
from app.services.pipeline_service import run_predictions_for_date


class DashboardAndPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        testing_session_local = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = testing_session_local()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _game(self, game_id: int, game_date: date | None = None) -> Game:
        game = Game(
            game_id=game_id,
            game_date=game_date or date.today(),
            season=2026,
            away_team="Away",
            home_team="Home",
            away_team_id=1,
            home_team_id=2,
            venue="Test Park",
            status="Preview",
            start_time="2026-04-18T18:00:00+00:00",
            away_pitcher_id=10,
            home_pitcher_id=20,
        )
        self.db.add(game)
        self.db.commit()
        return game

    def _prediction(self, game_id: int) -> Prediction:
        prediction = Prediction(
            game_id=game_id,
            model_version="v-test",
            run_stage="daily_open",
            is_active=True,
            sim_count=1000,
            away_win_pct=0.44,
            home_win_pct=0.56,
            projected_away_score=4.1,
            projected_home_score=4.8,
            projected_total=8.9,
            confidence_score=11.0,
            recommended_side="home_ml",
            using_xera=False,
            kbb_adv=0.061,
            park_factor_adv=0.025,
            pythagorean_win_pct_adv=0.084,
        )
        self.db.add(prediction)
        self.db.commit()
        self.db.refresh(prediction)
        return prediction

    def _odds(self, game_id: int) -> GameOdds:
        odds = GameOdds(
            game_id=game_id,
            sportsbook="draftkings",
            snapshot_type=SnapshotType.open,
            fetched_at=datetime.now(timezone.utc),
            away_ml=120,
            home_ml=-130,
            total_line=8.5,
            over_odds=-110,
            under_odds=-110,
        )
        self.db.add(odds)
        self.db.commit()
        self.db.refresh(odds)
        return odds

    def test_get_today_edges_reads_from_database_only(self) -> None:
        game = self._game(1)
        prediction = self._prediction(game.game_id)
        odds = self._odds(game.game_id)
        edge = EdgeResult(
            game_id=game.game_id,
            prediction_id=prediction.prediction_id,
            odds_id=odds.id,
            run_stage="daily_open",
            is_active=True,
            calculated_at=datetime.now(timezone.utc),
            model_away_win_pct=0.44,
            model_home_win_pct=0.56,
            implied_away_pct=0.47,
            implied_home_pct=0.53,
            edge_away=-0.03,
            edge_home=0.03,
            ev_away=-0.04,
            ev_home=0.05,
            recommended_play="home_ml",
            confidence_tier="medium",
            edge_pct=0.03,
            model_total=8.9,
            book_total=8.5,
            ev_over=0.02,
            ev_under=-0.01,
            movement_direction="toward_model",
        )
        self.db.add(edge)
        self.db.commit()

        rows = get_today_edges(db=self.db)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["game_id"], game.game_id)
        self.assertEqual(row["play"], "home_ml")
        self.assertEqual(row["confidence"], "medium")
        self.assertEqual(row["movement_direction"], "toward_model")
        self.assertAlmostEqual(row["kbb_adv"], prediction.kbb_adv)
        self.assertAlmostEqual(row["pythagorean_win_pct_adv"], prediction.pythagorean_win_pct_adv)
        self.assertAlmostEqual(row["park_factor_adv"], prediction.park_factor_adv)

    def test_run_predictions_for_date_uses_shared_pipeline_helper(self) -> None:
        game = self._game(2)

        with patch("app.services.pipeline_service.fetch_team_stats", side_effect=[{"team": "away"}, {"team": "home"}]), \
             patch("app.services.pipeline_service.fetch_pitcher_stats", side_effect=[{"xera": 3.5}, {"xera": 3.8}]), \
             patch("app.services.pipeline_service.fetch_bullpen_stats", side_effect=[{"bullpen": 1}, {"bullpen": 1}]), \
             patch("app.services.pipeline_service.fetch_team_statcast", side_effect=[{"statcast": 1}, {"statcast": 1}]), \
             patch("app.services.pipeline_service.build_team_features", side_effect=[{"starter_xera": 3.5}, {"starter_xera": 3.8, "park_run_factor": 1.0}]), \
             patch("app.services.pipeline_service.get_latest_calibration_result", return_value=None), \
             patch("app.services.pipeline_service.score_logistic_home_probability", return_value=0.54), \
             patch("app.services.pipeline_service.run_monte_carlo", return_value={
                 "sim_count": 1000,
                 "away_win_pct": 0.45,
                 "home_win_pct": 0.55,
                 "projected_away_score": 4.0,
                 "projected_home_score": 4.7,
                 "projected_total": 8.7,
                 "confidence_score": 9.5,
                 "recommended_side": "home_ml",
             }), \
             patch("app.services.pipeline_service.store_prediction") as store_prediction, \
             patch("app.services.pipeline_service.summarize_probability_diagnostics"):
            result = run_predictions_for_date(
                self.db,
                game.game_date,
                run_stage="daily_open",
                diagnostic_label="test-daily",
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["ran"], 1)
        self.assertEqual(result["errors"], [])
        store_prediction.assert_called_once()
        call_kwargs = store_prediction.call_args.kwargs
        self.assertEqual(call_kwargs["kbb_adv"], 0.0)
        self.assertEqual(call_kwargs["park_factor_adv"], 0.0)
        self.assertEqual(call_kwargs["pythagorean_win_pct_adv"], 0.0)


if __name__ == "__main__":
    unittest.main()
