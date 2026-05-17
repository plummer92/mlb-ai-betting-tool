from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.schema import (
    BetAlert,
    EdgeResult,
    Game,
    GameOdds,
    GameOutcomeReview,
    LineMovement,
    PaperTrade,
    Prediction,
    SnapshotType,
)
from app.services.market_audit_service import get_clv_report, get_movement_backtest_report


class MarketAuditServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        testing_session_local = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = testing_session_local()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _base_rows(self, game_id: int = 1) -> tuple[Game, Prediction, GameOdds, EdgeResult, BetAlert]:
        game = Game(
            game_id=game_id,
            game_date=date.today(),
            season=2026,
            away_team="Away",
            home_team="Home",
            away_team_id=1,
            home_team_id=2,
            status="Preview",
        )
        prediction = Prediction(
            game_id=game_id,
            model_version="v-test",
            run_stage="pregame",
            is_active=True,
            sim_count=1000,
            away_win_pct=0.55,
            home_win_pct=0.45,
            projected_away_score=4.6,
            projected_home_score=4.1,
            projected_total=8.7,
            confidence_score=1.0,
            recommended_side="away_ml",
        )
        entry_odds = GameOdds(
            game_id=game_id,
            sportsbook="draftkings",
            snapshot_type=SnapshotType.open,
            fetched_at=datetime.now(timezone.utc),
            away_ml=120,
            home_ml=-140,
            total_line=8.0,
            over_odds=-110,
            under_odds=-110,
        )
        self.db.add_all([game, prediction, entry_odds])
        self.db.commit()
        self.db.refresh(prediction)
        self.db.refresh(entry_odds)

        edge = EdgeResult(
            game_id=game_id,
            prediction_id=prediction.prediction_id,
            odds_id=entry_odds.id,
            run_stage="pregame",
            is_active=True,
            calculated_at=datetime.now(timezone.utc),
            model_away_win_pct=0.55,
            model_home_win_pct=0.45,
            implied_away_pct=0.45,
            implied_home_pct=0.55,
            edge_away=0.1,
            edge_home=-0.1,
            ev_away=0.12,
            ev_home=-0.2,
            recommended_play="away_ml",
            confidence_tier="strong",
            edge_pct=0.1,
            sportsbook="draftkings",
            odds_snapshot_type="open",
            away_ml=120,
            home_ml=-140,
            over_odds=-110,
            under_odds=-110,
        )
        self.db.add(edge)
        self.db.commit()
        self.db.refresh(edge)

        alert = BetAlert(
            game_id=game_id,
            prediction_id=prediction.prediction_id,
            edge_result_id=edge.id,
            game_date=game.game_date,
            play="away_ml",
            edge_pct=0.1,
            ev=0.12,
            confidence="strong",
            synopsis="test",
            status="sent",
        )
        self.db.add(alert)
        self.db.commit()
        self.db.refresh(alert)
        return game, prediction, entry_odds, edge, alert

    def test_clv_report_marks_bet_as_beating_close(self) -> None:
        _game, _prediction, _entry_odds, edge, alert = self._base_rows()
        self.db.add_all(
            [
                GameOdds(
                    game_id=1,
                    sportsbook="draftkings",
                    snapshot_type=SnapshotType.pregame,
                    fetched_at=datetime.now(timezone.utc),
                    away_ml=100,
                    home_ml=-120,
                    total_line=8.0,
                    over_odds=-110,
                    under_odds=-110,
                ),
                PaperTrade(
                    bet_alert_id=alert.id,
                    game_id=1,
                    prediction_id=alert.prediction_id,
                    edge_result_id=edge.id,
                    game_date=alert.game_date,
                    play="away_ml",
                    confidence="strong",
                    edge_pct=0.1,
                    ev=0.12,
                    odds=120,
                    status="open",
                ),
            ]
        )
        self.db.commit()

        report = get_clv_report(self.db)

        self.assertEqual(report["summary"]["bets"], 1)
        self.assertEqual(report["summary"]["beat_close"], 1)
        self.assertGreater(report["summary"]["avg_price_clv"], 0)

    def test_movement_backtest_groups_results(self) -> None:
        game, prediction, entry_odds, edge, _alert = self._base_rows(game_id=2)
        movement = LineMovement(
            game_id=game.game_id,
            sportsbook="consensus",
            open_away_ml=120,
            open_home_ml=-140,
            open_total=8.0,
            pregame_away_ml=100,
            pregame_home_ml=-120,
            pregame_total=8.5,
            away_prob_move=0.05,
            home_prob_move=-0.05,
            total_move=0.5,
            sharp_away=True,
            sharp_home=False,
            total_steam_over=True,
            total_steam_under=False,
        )
        self.db.add(movement)
        self.db.commit()
        self.db.refresh(movement)
        edge.movement_id = movement.id
        edge.movement_direction = "toward_model"
        self.db.add(
            GameOutcomeReview(
                game_id=game.game_id,
                prediction_id=prediction.prediction_id,
                edge_result_id=edge.id,
                game_date=game.game_date,
                actual_outcome_summary="summary",
                recommended_play="away_ml",
                confidence_tier="strong",
                edge_pct=0.1,
                ev=0.12,
                model_away_win_pct=0.55,
                model_home_win_pct=0.45,
                model_total=8.7,
                book_total=8.0,
                movement_direction="toward_model",
                final_away_score=5,
                final_home_score=3,
                winning_side="away",
                bet_result="win",
                was_model_correct=True,
            )
        )
        self.db.commit()

        report = get_movement_backtest_report(self.db, min_sample=1)

        self.assertEqual(report["summary"]["bets"], 1)
        self.assertEqual(report["by_movement_direction"][0]["movement_direction"], "toward_model")
        self.assertEqual(report["by_movement_bucket"][0]["movement_bucket"], "ml_steam")


if __name__ == "__main__":
    unittest.main()
