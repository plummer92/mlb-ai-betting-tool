from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.schema import BacktestResult, BetAlert, EdgeResult, Game, GameOdds, GameOutcomeReview, LineMovement, Prediction, SnapshotType
from app.routes.commentary import commentary_today
from app.routes.admin import admin_backfill_prediction_dashboard_metrics, admin_freshness
from app.routes.debug import _raw_edge_acceptance, build_decision_pipeline_diagnostics, build_odds_freshness_report, build_raw_edge_board
from app.routes.model import get_today_predictions, run_model
from app.routes.ranked import _build_ranked_rows, _decision_row_from_ranked
from app.routes.reviews import get_review_summary, profitability_report
from app.services.betting_policy import qualifies_for_bet_policy


class RouteAndAdminTests(unittest.TestCase):
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
        self.db.refresh(game)
        return game

    def _prediction(
        self,
        game_id: int,
        *,
        run_stage: str = "daily_open",
        away_win_pct: float = 0.46,
        home_win_pct: float = 0.54,
    ) -> Prediction:
        prediction = Prediction(
            game_id=game_id,
            model_version="v-test",
            run_stage=run_stage,
            is_active=True,
            sim_count=1000,
            away_win_pct=away_win_pct,
            home_win_pct=home_win_pct,
            projected_away_score=4.0,
            projected_home_score=4.6,
            projected_total=8.6,
            confidence_score=1.08,
            recommended_side="home_ml",
            home_starter_xera=3.7,
            away_starter_xera=4.1,
            using_xera=True,
            kbb_adv=0.05,
            park_factor_adv=0.01,
            pythagorean_win_pct_adv=0.03,
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

    def test_review_summary_reports_flagged_bet_record(self) -> None:
        game = self._game(1)
        prediction = self._prediction(game.game_id)
        review_win = GameOutcomeReview(
            game_id=game.game_id,
            prediction_id=prediction.prediction_id,
            game_date=game.game_date,
            actual_outcome_summary="Away won",
            recommended_play="away_ml",
            final_away_score=5,
            final_home_score=3,
            winning_side="away",
            bet_result="win",
            was_model_correct=True,
        )
        review_loss = GameOutcomeReview(
            game_id=game.game_id + 1,
            prediction_id=prediction.prediction_id,
            game_date=game.game_date,
            actual_outcome_summary="Home won",
            recommended_play="away_ml",
            final_away_score=1,
            final_home_score=4,
            winning_side="home",
            bet_result="loss",
            was_model_correct=False,
        )
        self.db.add_all([review_win, review_loss])
        self.db.commit()

        summary = get_review_summary(db=self.db)

        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 1)
        self.assertEqual(summary["bets_graded"], 2)
        self.assertEqual(summary["win_rate"], 0.5)

    def test_admin_freshness_and_backfill(self) -> None:
        game = self._game(2)
        prediction = self._prediction(game.game_id)
        prediction.kbb_adv = None
        prediction.park_factor_adv = None
        prediction.pythagorean_win_pct_adv = None
        self.db.commit()
        odds = self._odds(game.game_id)
        self.db.add(
            EdgeResult(
                game_id=game.game_id,
                prediction_id=prediction.prediction_id,
                odds_id=odds.id,
                run_stage="daily_open",
                is_active=True,
                calculated_at=datetime.now(timezone.utc),
                model_away_win_pct=0.46,
                model_home_win_pct=0.54,
                implied_away_pct=0.45,
                implied_home_pct=0.55,
                edge_away=0.01,
                edge_home=-0.01,
                ev_away=0.02,
                ev_home=-0.02,
                recommended_play="away_ml",
                confidence_tier="weak",
                edge_pct=0.01,
            )
        )
        self.db.commit()

        freshness = admin_freshness(db=self.db)
        self.assertEqual(freshness["games_today"], 1)
        self.assertEqual(freshness["active_predictions_today"], 1)

        with patch("app.services.admin_service.fetch_team_stats", side_effect=[{"team": "away"}, {"team": "home"}]), \
             patch("app.services.admin_service.fetch_pitcher_stats", side_effect=[{"xera": 4.1}, {"xera": 3.7}]), \
             patch("app.services.admin_service.fetch_bullpen_stats", side_effect=[{"bullpen": 1}, {"bullpen": 1}]), \
             patch("app.services.admin_service.fetch_team_statcast", side_effect=[{"statcast": 1}, {"statcast": 1}]), \
             patch("app.services.admin_service.build_team_features", side_effect=[{"starter_xera": 4.1}, {"starter_xera": 3.7, "park_run_factor": 1.0}]):
            result = admin_backfill_prediction_dashboard_metrics(db=self.db)

        self.assertEqual(result["updated"], 1)
        self.db.refresh(prediction)
        self.assertEqual(prediction.kbb_adv, 0.0)
        self.assertEqual(prediction.park_factor_adv, 0.0)
        self.assertEqual(prediction.pythagorean_win_pct_adv, 0.0)

    def test_run_model_persists_dashboard_metrics(self) -> None:
        game = self._game(3)
        self._odds(game.game_id)

        with patch("app.routes.model.fetch_team_stats", side_effect=[{"team": "away"}, {"team": "home"}]), \
             patch("app.routes.model.fetch_pitcher_stats", side_effect=[{"xera": 4.1}, {"xera": 3.7}]), \
             patch("app.routes.model.fetch_bullpen_stats", side_effect=[{"bullpen": 1}, {"bullpen": 1}]), \
             patch("app.routes.model.fetch_team_statcast", side_effect=[{"statcast": 1}, {"statcast": 1}]), \
             patch("app.routes.model.build_team_features", side_effect=[{"starter_xera": 4.1}, {"starter_xera": 3.7, "park_run_factor": 1.0}]), \
             patch("app.routes.model.get_latest_calibration_result", return_value=None), \
             patch("app.routes.model.score_logistic_home_probability", return_value=0.53), \
             patch("app.routes.model.run_monte_carlo", return_value={
                 "sim_count": 1000,
                 "away_win_pct": 0.45,
                 "home_win_pct": 0.55,
                 "projected_away_score": 4.1,
                 "projected_home_score": 4.9,
                 "projected_total": 9.0,
                 "confidence_score": 1.12,
                 "recommended_side": "home_ml",
             }), \
             patch("app.routes.model.summarize_probability_diagnostics"):
            prediction = run_model(game.game_id, db=self.db)

        self.assertEqual(prediction.kbb_adv, 0.0)
        self.assertEqual(prediction.park_factor_adv, 0.0)
        self.assertEqual(prediction.pythagorean_win_pct_adv, 0.0)

        rows = get_today_predictions(db=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["game_id"], game.game_id)
        self.assertEqual(rows[0]["kbb_adv"], 0.0)

    def test_commentary_route_has_single_domain_home(self) -> None:
        game = self._game(4)
        prediction = self._prediction(game.game_id)
        odds = self._odds(game.game_id)
        edge = EdgeResult(
            game_id=game.game_id,
            prediction_id=prediction.prediction_id,
            odds_id=odds.id,
            run_stage="daily_open",
            is_active=True,
            calculated_at=datetime.now(timezone.utc),
            model_away_win_pct=0.46,
            model_home_win_pct=0.54,
            implied_away_pct=0.45,
            implied_home_pct=0.55,
            edge_away=0.01,
            edge_home=-0.01,
            ev_away=0.02,
            ev_home=-0.02,
            recommended_play="away_ml",
            confidence_tier="medium",
            edge_pct=0.01,
        )
        alert = BetAlert(
            game_id=game.game_id,
            prediction_id=prediction.prediction_id,
            edge_result_id=999,
            game_date=game.game_date,
            play="away_ml",
            edge_pct=0.05,
            ev=0.03,
            confidence="medium",
            synopsis="Model likes the away side.",
            status="sent",
        )
        self.db.add_all([edge, alert])
        self.db.commit()

        payload = commentary_today(db=self.db)

        self.assertEqual(payload["source"], "alerts")
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["matchup"], "Away @ Home")

    def test_profitability_report_surfaces_market_segments(self) -> None:
        game = self._game(5)
        prediction = self._prediction(game.game_id)
        odds = self._odds(game.game_id)
        edge = EdgeResult(
            game_id=game.game_id,
            prediction_id=prediction.prediction_id,
            odds_id=odds.id,
            run_stage="daily_open",
            is_active=True,
            calculated_at=datetime.now(timezone.utc),
            model_away_win_pct=0.46,
            model_home_win_pct=0.54,
            implied_away_pct=0.45,
            implied_home_pct=0.55,
            edge_away=0.01,
            edge_home=-0.01,
            ev_away=0.02,
            ev_home=0.12,
            recommended_play="home_ml",
            confidence_tier="strong",
            edge_pct=0.08,
        )
        self.db.add(edge)
        self.db.flush()

        for idx, result in enumerate(["win", "win", "loss", "win", "win"], start=1):
            self.db.add(
                GameOutcomeReview(
                    game_id=game.game_id + idx,
                    prediction_id=prediction.prediction_id,
                    edge_result_id=edge.id,
                    game_date=game.game_date,
                    actual_outcome_summary="summary",
                    recommended_play="home_ml",
                    confidence_tier="strong",
                    edge_pct=0.08,
                    ev=0.12,
                    final_away_score=3,
                    final_home_score=5,
                    winning_side="home",
                    bet_result=result,
                    was_model_correct=result == "win",
                )
            )
        self.db.commit()

        report = profitability_report(db=self.db, min_sample=3)

        self.assertEqual(report["summary"]["total"], 5)
        self.assertTrue(any(row["play"] == "home_ml" for row in report["by_play"]))
        self.assertIn("profiles", report["policy_backtest"])

    def test_betting_policy_tightens_high_edge_tails(self) -> None:
        self.assertTrue(
            qualifies_for_bet_policy(
                play="home_ml",
                edge_pct=0.08,
                ev=0.12,
                confidence="strong",
            )
        )
        self.assertFalse(
            qualifies_for_bet_policy(
                play="away_ml",
                edge_pct=0.14,
                ev=0.18,
                confidence="strong",
            )
        )
        self.assertFalse(
            qualifies_for_bet_policy(
                play="under",
                edge_pct=0.07,
                ev=0.11,
                confidence="strong",
            )
        )

    def test_ranked_bets_use_trustworthy_active_edges_only(self) -> None:
        game = self._game(6)
        active_prediction = self._prediction(game.game_id)
        inactive_prediction = self._prediction(game.game_id)
        odds = self._odds(game.game_id)

        self.db.add_all(
            [
                EdgeResult(
                    game_id=game.game_id,
                    prediction_id=active_prediction.prediction_id,
                    odds_id=odds.id,
                    run_stage="daily_open",
                    is_active=True,
                    calculated_at=datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc),
                    model_away_win_pct=0.46,
                    model_home_win_pct=0.54,
                    implied_away_pct=0.45,
                    implied_home_pct=0.55,
                    edge_away=0.01,
                    edge_home=0.06,
                    ev_away=0.02,
                    ev_home=0.08,
                    recommended_play="home_ml",
                    confidence_tier="medium",
                    edge_pct=0.06,
                ),
                EdgeResult(
                    game_id=game.game_id,
                    prediction_id=inactive_prediction.prediction_id,
                    odds_id=odds.id,
                    run_stage="daily_open",
                    is_active=False,
                    calculated_at=datetime.now(timezone.utc),
                    model_away_win_pct=0.90,
                    model_home_win_pct=0.10,
                    implied_away_pct=0.45,
                    implied_home_pct=0.55,
                    edge_away=0.45,
                    edge_home=-0.45,
                    ev_away=0.50,
                    ev_home=-0.70,
                    recommended_play="away_ml",
                    confidence_tier="strong",
                    edge_pct=0.45,
                ),
            ]
        )
        self.db.commit()

        rows = _build_ranked_rows(db=self.db, limit=10, active_only=True)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["play"], "home_ml")
        self.assertEqual(rows[0]["edge_pct"], 0.06)

    def _decision_base_row(self, **overrides) -> dict:
        row = {
            "rank": 1,
            "game_id": 99,
            "matchup": "Away @ Home",
            "play": "home_ml",
            "raw_edge_pct": 0.05,
            "edge_pct": 0.05,
            "adjusted_edge_pct": 0.07,
            "market_respect_score": 82,
            "market_respect_tags": ["MARKET AGREED"],
            "market_trust_bucket": "strong_market_agreement",
            "odds_freshness_status": "FRESH",
            "movement_direction": "toward_model",
            "market_respect": {"score": 82, "tags": ["MARKET AGREED"], "components": {}},
            "market_respect_adjustment": {
                "score": 82,
                "tags": ["MARKET AGREED"],
                "explanation": "market agreed",
            },
        }
        row.update(overrides)
        return row

    def test_decision_queue_blocks_market_rejection(self) -> None:
        row = _decision_row_from_ranked(
            self._decision_base_row(
                market_respect_score=25,
                market_respect_tags=["MARKET REJECTED"],
                market_trust_bucket="market_rejection",
            )
        )

        self.assertEqual(row["decision_status"], "BLOCKED")

    def test_decision_queue_waits_for_stale_open(self) -> None:
        row = _decision_row_from_ranked(
            self._decision_base_row(
                market_respect_tags=["STALE OPEN"],
                odds_freshness_status="STALE",
            )
        )

        self.assertEqual(row["decision_status"], "WAIT FOR ODDS")

    def test_decision_queue_fires_on_strong_market_agreement(self) -> None:
        row = _decision_row_from_ranked(self._decision_base_row())

        self.assertEqual(row["decision_status"], "FIRE")

    def test_decision_queue_no_bet_on_negative_adjusted_edge(self) -> None:
        row = _decision_row_from_ranked(self._decision_base_row(adjusted_edge_pct=-0.01))

        self.assertEqual(row["decision_status"], "NO BET")

    def _pipeline_edge_game(
        self,
        game_id: int,
        *,
        movement_direction: str = "toward_model",
        sharp_home: bool = True,
        stale: bool = False,
        ev_home: float = 0.11,
        edge_pct: float = 0.08,
        minutes_to_start: int | None = None,
    ) -> None:
        game = self._game(game_id)
        if minutes_to_start is not None:
            game.start_time = (datetime.now(timezone.utc) + timedelta(minutes=minutes_to_start)).isoformat()
            self.db.commit()
        prediction = self._prediction(game.game_id)
        fetched_at = datetime.now(timezone.utc) - (timedelta(hours=4) if stale else timedelta(minutes=5))
        open_odds = self._odds(game.game_id)
        open_odds.fetched_at = fetched_at
        close_odds = GameOdds(
            game_id=game.game_id,
            sportsbook="draftkings",
            snapshot_type=SnapshotType.pregame,
            fetched_at=fetched_at,
            away_ml=120,
            home_ml=-155 if movement_direction == "toward_model" else -105,
            total_line=8.5,
            over_odds=-110,
            under_odds=-110,
        )
        movement = LineMovement(
            game_id=game.game_id,
            sportsbook="consensus",
            calculated_at=fetched_at,
            open_away_ml=120,
            open_home_ml=-130,
            open_total=8.5,
            pregame_away_ml=120,
            pregame_home_ml=-155 if movement_direction == "toward_model" else -105,
            pregame_total=8.5,
            away_prob_move=0.0,
            home_prob_move=0.04 if movement_direction == "toward_model" else -0.06,
            total_move=0.0,
            sharp_away=not sharp_home,
            sharp_home=sharp_home,
            total_steam_over=False,
            total_steam_under=False,
        )
        self.db.add_all([close_odds, movement])
        self.db.commit()
        self.db.refresh(open_odds)
        self.db.refresh(movement)
        edge = EdgeResult(
            game_id=game.game_id,
            prediction_id=prediction.prediction_id,
            odds_id=open_odds.id,
            movement_id=movement.id,
            run_stage="daily_open",
            is_active=True,
            calculated_at=datetime.now(timezone.utc),
            model_away_win_pct=0.46,
            model_home_win_pct=0.54,
            implied_away_pct=0.45,
            implied_home_pct=0.55,
            edge_away=-0.01,
            edge_home=edge_pct,
            ev_away=-0.02,
            ev_home=ev_home,
            recommended_play="home_ml",
            confidence_tier="strong",
            edge_pct=edge_pct,
            movement_direction=movement_direction,
            sportsbook="draftkings",
            away_ml=120,
            home_ml=-130,
        )
        self.db.add(edge)
        self.db.commit()

    def test_decision_pipeline_counts_surviving_play(self) -> None:
        self._pipeline_edge_game(91)

        report = build_decision_pipeline_diagnostics(self.db)

        self.assertEqual(report["counts"]["total_games"], 1)
        self.assertEqual(report["counts"]["games_with_odds"], 1)
        self.assertEqual(report["counts"]["games_with_model_projection"], 1)
        self.assertEqual(report["counts"]["raw_positive_edges"], 1)
        self.assertEqual(report["counts"]["final_ranked_plays"], 1)
        self.assertEqual(report["counts"]["fire_ready_plays"], 1)

    def test_decision_pipeline_tracks_rejection_reasons(self) -> None:
        self._pipeline_edge_game(92, movement_direction="away_from_model", sharp_home=False)
        self._pipeline_edge_game(93, stale=True)

        report = build_decision_pipeline_diagnostics(self.db)

        market_reasons = report["stages"]["after_market_respect_filter"]["top_rejection_reasons"]
        stale_reasons = report["stages"]["after_stale_odds_filter"]["top_rejection_reasons"]
        self.assertIn("market_rejection", {row["reason"] for row in market_reasons})
        self.assertIn("stale_feed", {row["reason"] for row in stale_reasons})

    def test_decision_pipeline_quiet_market_not_filtered_as_stale(self) -> None:
        self._pipeline_edge_game(95, stale=True, minutes_to_start=240)

        report = build_decision_pipeline_diagnostics(self.db)

        self.assertEqual(report["counts"]["after_stale_odds_filter"], 1)
        self.assertEqual(report["counts"]["final_ranked_plays"], 1)

    def test_odds_freshness_report_tracks_stale_feed(self) -> None:
        self._pipeline_edge_game(96, stale=True, minutes_to_start=30)

        report = build_odds_freshness_report(self.db)

        self.assertEqual(report["total_games"], 1)
        self.assertEqual(report["stale_games"], 1)
        self.assertTrue(any(row["reason"] == "stale_feed" for row in report["stale_reason_counts"]))

    def test_debug_routes_are_registered(self) -> None:
        from app.main import app

        paths = {route.path for route in app.routes}
        self.assertIn("/api/debug/routes", paths)
        self.assertIn("/api/debug/odds-freshness", paths)
        self.assertIn("/api/debug/raw-edge-board", paths)

    def test_raw_edge_board_returns_all_games(self) -> None:
        self._game(97)
        self._prediction(97)
        self._odds(97)
        self._game(98)
        self._prediction(98)
        self._odds(98)

        board = build_raw_edge_board(self.db)

        self.assertEqual(board["total_games"], 2)
        self.assertEqual({row["game_id"] for row in board["games"]}, {97, 98})

    def test_raw_edge_board_closest_to_positive_edge(self) -> None:
        self._game(99)
        self._prediction(99, run_stage="daily_open")
        self._odds(99)

        board = build_raw_edge_board(self.db)

        closest = board["summary"]["closest_to_positive_edge"]
        self.assertIsNotNone(closest)
        self.assertEqual(closest["game_id"], 99)
        self.assertEqual(closest["best_raw_edge_pct"], board["games"][0]["best_raw_edge_pct"])

    def test_raw_edge_board_positive_edge_count(self) -> None:
        self._game(100)
        self._prediction(100, home_win_pct=0.65, away_win_pct=0.35)
        self._odds(100)

        board = build_raw_edge_board(self.db)

        self.assertEqual(board["summary"]["count_edges_above_2"], 1)
        self.assertEqual(board["games"][0]["best_raw_play"], "home_ml")

    def test_raw_edge_threshold_comparisons(self) -> None:
        self.assertFalse(_raw_edge_acceptance({"best_raw_edge_pct": 0.0})["accepted"])
        accepted = _raw_edge_acceptance({"best_raw_edge_pct": 0.0001})
        self.assertTrue(accepted["accepted"])
        self.assertEqual(accepted["raw_edge_status"], "RAW_POSITIVE_EDGE")

    def test_raw_edge_debug_near_edge_behavior(self) -> None:
        with patch("app.routes.debug.DEBUG", True):
            accepted = _raw_edge_acceptance({"best_raw_edge_pct": -0.004})

        self.assertTrue(accepted["accepted"])
        self.assertEqual(accepted["raw_edge_status"], "DEBUG_NEAR_EDGE")
        self.assertEqual(accepted["reason"], "debug_near_edge")

    def test_decision_pipeline_tracks_policy_rejection_reasons(self) -> None:
        self._pipeline_edge_game(94, ev_home=0.01)

        report = build_decision_pipeline_diagnostics(self.db)

        confidence_reasons = report["stages"]["after_confidence_filter"]["top_rejection_reasons"]
        self.assertIn("ev_below_threshold", {row["reason"] for row in confidence_reasons})
        self.assertEqual(report["counts"]["after_confidence_filter"], 0)
        self.assertEqual(report["counts"]["final_ranked_plays"], 0)

    def test_decision_pipeline_zero_play_state(self) -> None:
        report = build_decision_pipeline_diagnostics(self.db)

        self.assertEqual(report["counts"]["total_games"], 0)
        self.assertEqual(report["counts"]["final_ranked_plays"], 0)


class SchedulerPathTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        testing_session_local = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = testing_session_local()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    async def test_calculate_edges_job_reuses_fresh_open_snapshot(self) -> None:
        game = Game(
            game_id=7,
            game_date=date.today(),
            season=2026,
            away_team="Away",
            home_team="Home",
            away_team_id=1,
            home_team_id=2,
        )
        prediction = Prediction(
            game_id=7,
            model_version="v-test",
            run_stage="daily_open",
            is_active=True,
            sim_count=1000,
            away_win_pct=0.45,
            home_win_pct=0.55,
            projected_away_score=4.2,
            projected_home_score=4.8,
            projected_total=9.0,
            confidence_score=1.0,
            recommended_side="home_ml",
            using_xera=False,
        )
        odds = GameOdds(
            game_id=7,
            sportsbook="draftkings",
            snapshot_type=SnapshotType.open,
            fetched_at=datetime.now(timezone.utc),
            away_ml=120,
            home_ml=-130,
        )
        self.db.add_all([game, prediction, odds])
        self.db.commit()
        self.db.refresh(odds)

        with patch("app.scheduler.SessionLocal", return_value=self.db), \
             patch("app.scheduler.calculate_edges_for_today", return_value={"status": "ok", "calculated": 1}) as calc_mock, \
             patch("app.scheduler.fetch_and_store_odds", new_callable=AsyncMock) as odds_mock:
            from app.scheduler import calculate_edges_job

            await calculate_edges_job()

        calc_mock.assert_called_once()
        self.assertEqual(calc_mock.call_args.kwargs["odds_rows"][0].id, odds.id)
        odds_mock.assert_not_called()

    def _backtest_result(self, accuracy: float) -> BacktestResult:
        result = BacktestResult(
            seasons="2022,2023,2024",
            n_games=100,
            accuracy=accuracy,
            cv_accuracy=accuracy,
            brier_score=0.22,
            log_loss=0.68,
            calibration_params_json='{"a": 1.0, "b": 0.0}',
            coefficients_json='{"run_diff_adv": 0.2, "pythagorean_win_pct_adv": 0.3}',
            feature_ranks_json="[]",
        )
        self.db.add(result)
        self.db.commit()
        self.db.refresh(result)
        return result

    def test_weekly_backtest_discards_worse_candidate(self) -> None:
        old = self._backtest_result(0.57)
        old_id = old.id

        def create_candidate(db, seasons, apply_weights=True):
            self.assertFalse(apply_weights)
            return self._backtest_result(0.56)

        with patch("app.scheduler.SessionLocal", return_value=self.db), \
             patch("app.scheduler.run_logistic_regression", side_effect=create_candidate), \
             patch("app.scheduler.apply_backtest_weights") as apply_mock, \
             patch("app.services.notification_service.send_alert_message", return_value=(True, None)) as notify_mock:
            from app.scheduler import weekly_backtest_job

            weekly_backtest_job()

        apply_mock.assert_not_called()
        notify_mock.assert_called_once()
        rows = self.db.query(BacktestResult).all()
        self.assertEqual([row.id for row in rows], [old_id])

    def test_weekly_backtest_deploys_better_candidate(self) -> None:
        old = self._backtest_result(0.56)
        old_id = old.id

        def create_candidate(db, seasons, apply_weights=True):
            self.assertFalse(apply_weights)
            return self._backtest_result(0.57)

        with patch("app.scheduler.SessionLocal", return_value=self.db), \
             patch("app.scheduler.run_logistic_regression", side_effect=create_candidate), \
             patch("app.scheduler.apply_backtest_weights") as apply_mock, \
             patch("app.services.notification_service.send_alert_message") as notify_mock:
            from app.scheduler import weekly_backtest_job

            weekly_backtest_job()

        apply_mock.assert_called_once()
        notify_mock.assert_not_called()
        rows = self.db.query(BacktestResult).order_by(BacktestResult.id.asc()).all()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].id, old_id)
        self.assertEqual(rows[1].accuracy, 0.57)


if __name__ == "__main__":
    unittest.main()
