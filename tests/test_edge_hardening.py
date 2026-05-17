from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.schema import BacktestResult, EdgeResult, Game, GameOdds, Prediction, SnapshotType
from app.routes.edges import get_top_edges
from app.services.backtest_service import apply_calibration
from app.services.edge_service import calculate_all_edges_today, calculate_edge_for_game
from app.services.odds_service import is_odds_snapshot_fresh


class EdgeHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        TestingSessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = TestingSessionLocal()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _game(self, game_id: int, game_date: date) -> Game:
        game = Game(
            game_id=game_id,
            game_date=game_date,
            season=2026,
            away_team="Away",
            home_team="Home",
            away_team_id=1,
            home_team_id=2,
            venue="Test Park",
            status="Preview",
            start_time="2026-04-02T18:00:00+00:00",
        )
        self.db.add(game)
        self.db.commit()
        return game

    def _prediction(
        self,
        game_id: int,
        *,
        run_stage: str = "daily_open",
        away_win_pct: float = 0.45,
        home_win_pct: float = 0.55,
        calibrated_away_win_pct: float | None = None,
        calibrated_home_win_pct: float | None = None,
        is_active: bool = True,
    ) -> Prediction:
        prediction = Prediction(
            game_id=game_id,
            model_version="v-test",
            run_stage=run_stage,
            is_active=is_active,
            sim_count=1000,
            away_win_pct=away_win_pct,
            home_win_pct=home_win_pct,
            calibrated_away_win_pct=calibrated_away_win_pct,
            calibrated_home_win_pct=calibrated_home_win_pct,
            projected_away_score=4.1,
            projected_home_score=4.6,
            projected_total=8.7,
            confidence_score=10.0,
            recommended_side="HOME",
            using_xera=False,
        )
        self.db.add(prediction)
        self.db.commit()
        self.db.refresh(prediction)
        return prediction

    def _odds(
        self,
        game_id: int,
        *,
        snapshot_type: SnapshotType = SnapshotType.open,
        fetched_at: datetime | None = None,
        away_ml: int = 120,
        home_ml: int = -130,
        total_line: float = 8.5,
        over_odds: int = -110,
        under_odds: int = -110,
    ) -> GameOdds:
        odds = GameOdds(
            game_id=game_id,
            sportsbook="draftkings",
            snapshot_type=snapshot_type,
            fetched_at=fetched_at or datetime.now(timezone.utc),
            away_ml=away_ml,
            home_ml=home_ml,
            total_line=total_line,
            over_odds=over_odds,
            under_odds=under_odds,
        )
        self.db.add(odds)
        self.db.commit()
        self.db.refresh(odds)
        return odds

    def test_odds_snapshot_freshness(self) -> None:
        fresh = self._odds(1, fetched_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        stale = self._odds(2, fetched_at=datetime.now(timezone.utc) - timedelta(hours=6))
        self.assertTrue(is_odds_snapshot_fresh(fresh))
        self.assertFalse(is_odds_snapshot_fresh(stale))

    def test_no_edge_created_when_odds_missing(self) -> None:
        self._game(10, date.today())
        self._prediction(10)
        result = calculate_edge_for_game(
            self.db,
            10,
            run_stage="daily_open",
            snapshot_type=SnapshotType.open,
            odds_snapshot=None,
            fallback_policy="none",
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "missing_explicit_odds_snapshot")

    def test_no_edge_created_when_odds_stale(self) -> None:
        self._game(11, date.today())
        self._prediction(11)
        stale = self._odds(11, fetched_at=datetime.now(timezone.utc) - timedelta(hours=8))
        result = calculate_edge_for_game(
            self.db,
            11,
            run_stage="daily_open",
            snapshot_type=SnapshotType.open,
            odds_snapshot=stale,
            fallback_policy="none",
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "stale_explicit_odds_snapshot")

    def test_fallback_reuses_fresh_same_stage_snapshot(self) -> None:
        self._game(111, date.today())
        self._prediction(111)
        self._odds(111, fetched_at=datetime.now(timezone.utc) - timedelta(minutes=20))
        result = calculate_edge_for_game(
            self.db,
            111,
            run_stage="daily_open",
            snapshot_type=SnapshotType.open,
            odds_snapshot=None,
            fallback_policy="reuse_fresh_same_stage",
        )
        self.assertEqual(result["status"], "created")

    def test_fallback_uses_fresh_db_snapshot_when_explicit_snapshot_is_stale(self) -> None:
        self._game(112, date.today())
        self._prediction(112)
        fresh = self._odds(112, fetched_at=datetime.now(timezone.utc) - timedelta(minutes=10))
        stale = GameOdds(
            game_id=112,
            sportsbook="draftkings",
            snapshot_type=SnapshotType.open,
            fetched_at=datetime.now(timezone.utc) - timedelta(hours=8),
            away_ml=120,
            home_ml=-130,
            total_line=8.5,
            over_odds=-110,
            under_odds=-110,
        )
        result = calculate_edge_for_game(
            self.db,
            112,
            run_stage="daily_open",
            snapshot_type=SnapshotType.open,
            odds_snapshot=stale,
            fallback_policy="reuse_fresh_same_stage",
        )
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["odds_id"], fresh.id)

    def test_fallback_uses_matching_db_snapshot_when_explicit_snapshot_type_is_wrong(self) -> None:
        self._game(113, date.today())
        self._prediction(113)
        fresh = self._odds(113, snapshot_type=SnapshotType.open, fetched_at=datetime.now(timezone.utc) - timedelta(minutes=10))
        wrong_type = GameOdds(
            game_id=113,
            sportsbook="draftkings",
            snapshot_type=SnapshotType.pregame,
            fetched_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            away_ml=120,
            home_ml=-130,
            total_line=8.5,
            over_odds=-110,
            under_odds=-110,
        )
        result = calculate_edge_for_game(
            self.db,
            113,
            run_stage="daily_open",
            snapshot_type=SnapshotType.open,
            odds_snapshot=wrong_type,
            fallback_policy="reuse_fresh_same_stage",
        )
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["odds_id"], fresh.id)

    def test_calculate_all_edges_today_uses_db_lookup_when_explicit_rows_are_partial(self) -> None:
        today = date.today()
        explicit_rows = []
        for game_id in (301, 302, 303, 304):
            self._game(game_id, today)
            self._prediction(game_id)
        for game_id in (301, 302, 303):
            explicit_rows.append(
                self._odds(game_id, snapshot_type=SnapshotType.open, fetched_at=datetime.now(timezone.utc) - timedelta(minutes=10))
            )

        results = calculate_all_edges_today(
            self.db,
            run_stage="daily_open",
            snapshot_type=SnapshotType.open,
            odds_rows=explicit_rows,
            fallback_policy="reuse_fresh_same_stage",
        )

        by_game = {row["game_id"]: row for row in results}
        self.assertEqual(by_game[301]["status"], "created")
        self.assertEqual(by_game[302]["status"], "created")
        self.assertEqual(by_game[303]["status"], "created")
        self.assertEqual(by_game[304]["status"], "skipped")
        self.assertEqual(by_game[304]["reason"], "missing_odds_snapshot")

    def test_invalid_probabilities_are_quarantined(self) -> None:
        self._game(12, date.today())
        self._prediction(12, away_win_pct=0.0, home_win_pct=1.0)
        odds = self._odds(12)
        result = calculate_edge_for_game(
            self.db,
            12,
            run_stage="daily_open",
            snapshot_type=SnapshotType.open,
            odds_snapshot=odds,
            fallback_policy="none",
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "invalid_model_probability")

    def test_edge_pct_tracks_selected_play_not_largest_board_edge(self) -> None:
        self._game(13, date.today())
        self._prediction(
            13,
            away_win_pct=0.49,
            home_win_pct=0.51,
        )
        odds = self._odds(
            13,
            away_ml=300,
            home_ml=-500,
            total_line=8.5,
            over_odds=-110,
            under_odds=-110,
        )

        result = calculate_edge_for_game(
            self.db,
            13,
            run_stage="daily_open",
            snapshot_type=SnapshotType.open,
            odds_snapshot=odds,
            fallback_policy="none",
        )

        self.assertEqual(result["status"], "created")
        edge = result["edge"]
        self.assertEqual(edge.recommended_play, "over")
        self.assertLess(edge.edge_pct, abs(edge.edge_away))
        self.assertGreater(edge.ev_over, 0)

    def test_edges_top_defaults_to_today_only(self) -> None:
        today = date.today()
        old_day = today - timedelta(days=2)
        self._game(20, today)
        self._game(21, old_day)
        today_prediction = self._prediction(20, run_stage="daily_open")
        old_prediction = self._prediction(21, run_stage="daily_open")
        today_odds = self._odds(20)
        old_odds = self._odds(21)

        today_edge = EdgeResult(
            game_id=20,
            prediction_id=today_prediction.prediction_id,
            odds_id=today_odds.id,
            run_stage="daily_open",
            is_active=True,
            calculated_at=datetime.now(timezone.utc),
            model_away_win_pct=0.45,
            model_home_win_pct=0.55,
            implied_away_pct=0.47,
            implied_home_pct=0.53,
            edge_away=-0.02,
            edge_home=0.02,
            ev_away=-0.05,
            ev_home=0.03,
            recommended_play="home_ml",
            confidence_tier="weak",
            edge_pct=0.02,
        )
        old_edge = EdgeResult(
            game_id=21,
            prediction_id=old_prediction.prediction_id,
            odds_id=old_odds.id,
            run_stage="daily_open",
            is_active=True,
            calculated_at=datetime.now(timezone.utc) - timedelta(days=2),
            model_away_win_pct=0.1,
            model_home_win_pct=0.9,
            implied_away_pct=0.5,
            implied_home_pct=0.5,
            edge_away=-0.4,
            edge_home=0.4,
            ev_away=-0.8,
            ev_home=0.7,
            recommended_play="home_ml",
            confidence_tier="strong",
            edge_pct=0.4,
        )
        self.db.add_all([today_edge, old_edge])
        self.db.commit()

        rows = get_top_edges(limit=10, include_all_dates=False, db=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["game_id"], 20)

    def test_edges_top_excludes_legacy_active_edges(self) -> None:
        today = date.today()
        self._game(30, today)
        prediction = self._prediction(30, run_stage="legacy")
        odds = self._odds(30)
        edge = EdgeResult(
            game_id=30,
            prediction_id=prediction.prediction_id,
            odds_id=odds.id,
            run_stage="legacy",
            is_active=True,
            calculated_at=datetime.now(timezone.utc),
            model_away_win_pct=0.45,
            model_home_win_pct=0.55,
            implied_away_pct=0.47,
            implied_home_pct=0.53,
            edge_away=-0.02,
            edge_home=0.02,
            ev_away=-0.05,
            ev_home=0.03,
            recommended_play="home_ml",
            confidence_tier="weak",
            edge_pct=0.02,
        )
        self.db.add(edge)
        self.db.commit()

        rows = get_top_edges(limit=10, include_all_dates=False, db=self.db)
        self.assertEqual(rows, [])
        self.db.refresh(edge)
        self.assertFalse(edge.is_active)

    def test_edges_top_excludes_stale_current_edges(self) -> None:
        today = date.today()
        self._game(31, today)
        prediction = self._prediction(31, run_stage="daily_open")
        odds = self._odds(31, fetched_at=datetime.now(timezone.utc) - timedelta(hours=6))
        edge = EdgeResult(
            game_id=31,
            prediction_id=prediction.prediction_id,
            odds_id=odds.id,
            run_stage="daily_open",
            is_active=True,
            calculated_at=datetime.now(timezone.utc),
            model_away_win_pct=0.45,
            model_home_win_pct=0.55,
            implied_away_pct=0.47,
            implied_home_pct=0.53,
            edge_away=-0.02,
            edge_home=0.02,
            ev_away=-0.05,
            ev_home=0.03,
            recommended_play="home_ml",
            confidence_tier="weak",
            edge_pct=0.02,
        )
        self.db.add(edge)
        self.db.commit()

        rows = get_top_edges(limit=10, include_all_dates=False, db=self.db)
        self.assertEqual(rows, [])
        self.db.refresh(edge)
        self.assertFalse(edge.is_active)

    def test_calibration_preserves_side_orientation(self) -> None:
        cal_home, cal_away = apply_calibration(0.72, 0.28, {"a": 4.197193, "b": -2.114353})
        self.assertGreater(cal_home, cal_away)


if __name__ == "__main__":
    unittest.main()
