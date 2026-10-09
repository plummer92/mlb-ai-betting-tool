from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.schema import (
    Game,
    GameOdds,
    GameOutcomeReview,
    Prediction,
    SharpMoveJournal,
    SnapshotType,
    TradableDecisionSnapshot,
)
from app.services.market_replay_service import build_market_replay, list_market_replays


class MarketReplayServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        self.session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)()
        Base.metadata.create_all(bind=self.engine)

    def tearDown(self) -> None:
        self.session.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _game(self) -> tuple[Game, Prediction]:
        game = Game(
            game_id=901,
            game_date=date(2026, 8, 19),
            season=2026,
            away_team="Away Club",
            home_team="Home Club",
            status="Final",
            start_time="2026-08-19T23:10:00+00:00",
            final_away_score=3,
            final_home_score=5,
        )
        prediction = Prediction(
            game_id=901,
            model_version="v-test",
            run_stage="daily_open",
            is_active=True,
            sim_count=1000,
            away_win_pct=0.45,
            home_win_pct=0.55,
            projected_away_score=3.8,
            projected_home_score=4.6,
            projected_total=8.4,
            confidence_score=0.7,
            recommended_side="home_ml",
        )
        self.session.add_all([game, prediction])
        self.session.commit()
        self.session.refresh(prediction)
        return game, prediction

    def test_replay_keeps_auditable_line_timeline_and_decision(self) -> None:
        game, prediction = self._game()
        now = datetime.now(timezone.utc)
        self.session.add_all([
            GameOdds(
                game_id=game.game_id, sportsbook="draftkings", snapshot_type=SnapshotType.open,
                fetched_at=now - timedelta(hours=4), away_ml=115, home_ml=-135,
                total_line=8.5, over_odds=-110, under_odds=-110,
            ),
            GameOdds(
                game_id=game.game_id, sportsbook="draftkings", snapshot_type=SnapshotType.pregame,
                fetched_at=now - timedelta(minutes=20), away_ml=120, home_ml=-140,
                total_line=8.0, over_odds=-105, under_odds=-115,
            ),
            TradableDecisionSnapshot(
                game_id=game.game_id, game_date=game.game_date, matchup="Away Club @ Home Club",
                play="under", decision_status="BLOCKED", tradable_signal="WATCH",
                decision_reason="No positive CLV.", policy_status="BLOCKED", policy_score=39,
                trade_allowed=False,
            ),
            SharpMoveJournal(
                game_id=game.game_id, game_date=game.game_date, matchup="Away Club @ Home Club",
                play="under", sportsbook="draftkings", movement_bucket="total_steam",
                market_signal="TOTAL_STEAM_UNDER", line_move=0.5, price_move=0.02,
                readiness="CLV_READY",
            ),
            GameOutcomeReview(
                game_id=game.game_id, prediction_id=prediction.prediction_id, game_date=game.game_date,
                actual_outcome_summary="Home Club won 5-3.", recommended_play="under",
                final_away_score=3, final_home_score=5, winning_side="home",
                bet_result="push", was_model_correct=True,
            ),
        ])
        self.session.commit()

        replay = build_market_replay(self.session, game.game_id)

        self.assertEqual(replay["game"]["matchup"], "Away Club @ Home Club")
        self.assertEqual(replay["outcome"]["final_score"], "3-5")
        self.assertEqual(replay["decision"]["status"], "BLOCKED")
        self.assertFalse(replay["decision"]["trade_allowed"])
        self.assertEqual(len(replay["odds_timeline"]), 2)
        self.assertEqual(replay["source_summary"][0]["source"], "draftkings")
        self.assertEqual(replay["sharp_move_journal"][0]["market_signal"], "TOTAL_STEAM_UNDER")

    def test_replay_index_lists_completed_games_only(self) -> None:
        game, prediction = self._game()
        self.session.add(GameOutcomeReview(
            game_id=game.game_id, prediction_id=prediction.prediction_id, game_date=game.game_date,
            actual_outcome_summary="Home Club won.", final_away_score=3, final_home_score=5,
            winning_side="home", bet_result="no_bet", was_model_correct=True,
        ))
        self.session.commit()

        result = list_market_replays(self.session, limit=5)

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["rows"][0]["game_id"], game.game_id)
        self.assertEqual(result["rows"][0]["final_score"], "3-5")

    def test_replay_index_batches_latest_reviews_with_timestamp_ties(self) -> None:
        game, prediction = self._game()
        new_prediction = Prediction(
            game_id=game.game_id, away_win_pct=0.45, home_win_pct=0.55,
            projected_away_score=3.8, projected_home_score=4.6,
            projected_total=8.4, confidence_score=0.7,
        )
        self.session.add(new_prediction)
        timestamp = datetime(2026, 8, 20, tzinfo=timezone.utc)
        self.session.add_all([
            Game(game_id=902, game_date=game.game_date, season=2026,
                 away_team="Other Away", home_team="Other Home", status="Final",
                 final_away_score=1, final_home_score=2),
            GameOutcomeReview(
                game_id=game.game_id, prediction_id=prediction.prediction_id,
                game_date=game.game_date, created_at=timestamp,
                actual_outcome_summary="Old review", final_away_score=3,
                final_home_score=5, winning_side="home", recommended_play="over", bet_result="loss",
            ),
        ])
        self.session.flush()
        self.session.add(GameOutcomeReview(
            game_id=game.game_id, prediction_id=new_prediction.prediction_id,
            game_date=game.game_date, created_at=timestamp,
            actual_outcome_summary="New review", final_away_score=3,
            final_home_score=5, winning_side="home", recommended_play="under", bet_result="push",
        ))
        self.session.commit()

        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            result = list_market_replays(self.session, limit=5)
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)

        self.assertEqual(len(statements), 4)
        self.assertEqual(result["count"], 2)
        rows = {row["game_id"]: row for row in result["rows"]}
        self.assertEqual(rows[901]["recommended_play"], "under")
        self.assertEqual(rows[901]["bet_result"], "push")
        self.assertIsNone(rows[902]["bet_result"])

    def test_empty_replay_routes_and_unknown_game(self) -> None:
        from app.routes.reviews import market_replay, market_replays

        self.assertEqual(market_replays(limit=5, db=self.session),
                         {"status": "ok", "count": 0, "rows": []})
        self.assertEqual(market_replay(999, db=self.session),
                         {"status": "not_found", "game_id": 999})

    def test_replay_without_related_data(self) -> None:
        self.session.add(Game(game_id=903, game_date=date(2026, 8, 19), season=2026,
                              away_team="Away", home_team="Home", status="Preview"))
        self.session.commit()
        replay = build_market_replay(self.session, 903)
        self.assertEqual(replay["odds_timeline"], [])
        self.assertEqual(replay["source_summary"], [])
        self.assertEqual(replay["sharp_move_journal"], [])
        self.assertIsNone(replay["outcome"]["final_score"])
        self.assertFalse(replay["decision"]["trade_allowed"])
        self.assertEqual(list_market_replays(self.session)["count"], 0)
