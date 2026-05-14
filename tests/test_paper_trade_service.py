from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.schema import BetAlert, EdgeResult, Game, GameOdds, Prediction, SnapshotType
from app.services.paper_trade_service import get_paper_summary, log_alert_as_paper_trade


class PaperTradeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        testing_session_local = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = testing_session_local()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _seed_alert(self, *, game_id: int, play: str, confidence: str, result: str | None) -> tuple[BetAlert, EdgeResult]:
        game = Game(
            game_id=game_id,
            game_date=date.today(),
            season=2026,
            away_team="Away",
            home_team="Home",
        )
        prediction = Prediction(
            game_id=game_id,
            model_version="v-test",
            away_win_pct=0.45,
            home_win_pct=0.55,
            projected_away_score=4.1,
            projected_home_score=4.6,
            projected_total=8.7,
            confidence_score=10.0,
            recommended_side=play,
        )
        odds = GameOdds(
            game_id=game_id,
            sportsbook="draftkings",
            snapshot_type=SnapshotType.open,
            fetched_at=datetime.now(timezone.utc),
            away_ml=120,
            home_ml=-130,
            total_line=8.5,
            over_odds=-110,
            under_odds=-105,
        )
        self.db.add_all([game, prediction, odds])
        self.db.commit()
        self.db.refresh(prediction)
        self.db.refresh(odds)

        edge = EdgeResult(
            game_id=game_id,
            prediction_id=prediction.prediction_id,
            odds_id=odds.id,
            run_stage="daily_open",
            model_away_win_pct=0.45,
            model_home_win_pct=0.55,
            implied_away_pct=0.47,
            implied_home_pct=0.53,
            edge_away=-0.02,
            edge_home=0.02,
            ev_away=-0.03,
            ev_home=0.08,
            model_total=8.7,
            book_total=8.5,
            ev_over=0.05,
            ev_under=-0.02,
            recommended_play=play,
            confidence_tier=confidence,
            edge_pct=0.07,
            sportsbook="draftkings",
            away_ml=120,
            home_ml=-130,
            over_odds=-110,
            under_odds=-105,
        )
        self.db.add(edge)
        self.db.commit()
        self.db.refresh(edge)

        alert = BetAlert(
            game_id=game_id,
            prediction_id=prediction.prediction_id,
            edge_result_id=edge.id,
            game_date=game.game_date,
            play=play,
            edge_pct=edge.edge_pct,
            ev=edge.ev_home if play == "home_ml" else edge.ev_over,
            confidence=confidence,
            synopsis="alert",
            status="sent",
            bet_result=result,
        )
        self.db.add(alert)
        self.db.commit()
        self.db.refresh(alert)
        return alert, edge

    def test_log_alert_as_paper_trade_and_summary(self) -> None:
        winning_alert, winning_edge = self._seed_alert(
            game_id=1,
            play="home_ml",
            confidence="strong",
            result="win",
        )
        losing_alert, losing_edge = self._seed_alert(
            game_id=2,
            play="over",
            confidence="medium",
            result="loss",
        )

        log_alert_as_paper_trade(self.db, winning_alert, winning_edge)
        log_alert_as_paper_trade(self.db, losing_alert, losing_edge)
        log_alert_as_paper_trade(self.db, losing_alert, losing_edge)
        self.db.commit()

        summary = get_paper_summary(self.db)

        self.assertEqual(summary["actual_bet_alerts"], 2)
        self.assertEqual(summary["paper_bets_placed"], 2)
        self.assertEqual(summary["missing_paper_trades"], 0)
        self.assertEqual(summary["settled_trades"], 2)
        by_play = {row["play"]: row for row in summary["win_rate_by_play_type"]}
        self.assertEqual(by_play["home_ml"]["wins"], 1)
        self.assertEqual(by_play["over"]["losses"], 1)
        by_confidence = {row["confidence"]: row for row in summary["roi_by_confidence"]}
        self.assertGreater(by_confidence["strong"]["roi"], 0)
        self.assertLess(by_confidence["medium"]["roi"], 0)


if __name__ == "__main__":
    unittest.main()
