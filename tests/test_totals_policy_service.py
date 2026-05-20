from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.schema import EdgeResult, Game, GameOdds, Prediction, SandboxPredictionV4, SnapshotType
from app.services.totals_policy_service import apply_under_cluster_risk, evaluate_totals_policy


class TotalsPolicyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        testing_session_local = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = testing_session_local()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _edge_game(
        self,
        *,
        game_id: int = 1,
        play: str = "under",
        model_total: float = 7.0,
        book_total: float = 9.0,
        wind_factor: float | None = None,
        bullpen_strength: float = 1.0,
        park_factor_adv: float = 0.0,
    ) -> tuple[EdgeResult, Game, Prediction, GameOdds]:
        game = Game(
            game_id=game_id,
            game_date=date.today(),
            season=2026,
            away_team="Away",
            home_team="Home",
            away_team_id=1,
            home_team_id=2,
            status="Preview",
            start_time="2026-05-18T20:00:00+00:00",
            away_probable_pitcher="Away Starter",
            home_probable_pitcher="Home Starter",
        )
        prediction = Prediction(
            game_id=game_id,
            model_version="v-test",
            run_stage="daily_open",
            is_active=True,
            sim_count=1000,
            away_win_pct=0.5,
            home_win_pct=0.5,
            projected_away_score=model_total / 2,
            projected_home_score=model_total / 2,
            projected_total=model_total,
            confidence_score=1.0,
            recommended_side=play,
            using_xera=False,
            park_factor_adv=park_factor_adv,
        )
        odds = GameOdds(
            game_id=game_id,
            sportsbook="draftkings",
            snapshot_type=SnapshotType.open,
            fetched_at=datetime.now(timezone.utc),
            away_ml=100,
            home_ml=-120,
            total_line=book_total,
            over_odds=-110,
            under_odds=-110,
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
            is_active=True,
            model_total=model_total,
            book_total=book_total,
            total_edge=model_total - book_total,
            ev_over=-0.02 if play == "under" else 0.1,
            ev_under=0.1 if play == "under" else -0.02,
            recommended_play=play,
            confidence_tier="strong",
            edge_pct=0.1,
            over_odds=-110,
            under_odds=-110,
        )
        self.db.add(edge)
        if wind_factor is not None:
            self.db.add(
                SandboxPredictionV4(
                    game_id=game_id,
                    game_date=date.today(),
                    away_team="Away",
                    home_team="Home",
                    wind_factor=wind_factor,
                    home_bullpen_strength=bullpen_strength,
                    away_bullpen_strength=bullpen_strength,
                )
            )
        self.db.commit()
        self.db.refresh(edge)
        return edge, game, prediction, odds

    def _respect(
        self,
        *,
        score: int = 80,
        line_clv: float | None = 0.5,
        rejected: bool = False,
        movement_direction: str = "toward_model",
        late_move: bool = False,
        sharp_match: bool = True,
    ) -> dict:
        return {
            "score": 25 if rejected else score,
            "tags": ["MARKET REJECTED"] if rejected else ["MARKET AGREED"],
            "components": {
                "line_clv": line_clv,
                "price_clv": None,
                "movement_direction": movement_direction,
                "line_move": 0.5,
                "sharp_match": sharp_match,
                "sharp_against": False,
                "late_move": late_move,
                "became_more_expensive": True,
            },
        }

    def test_near_market_totals_are_blocked(self) -> None:
        edge, game, prediction, odds = self._edge_game(model_total=8.8, book_total=9.0)

        policy = evaluate_totals_policy(self.db, edge=edge, game=game, prediction=prediction, odds=odds, market_respect=self._respect())

        self.assertEqual(policy["policy_status"], "BLOCKED")
        self.assertIn("near_market_noise", policy["policy_reasons"])

    def test_medium_edge_requires_positive_clv(self) -> None:
        edge, game, prediction, odds = self._edge_game(model_total=7.7, book_total=9.0)

        policy = evaluate_totals_policy(self.db, edge=edge, game=game, prediction=prediction, odds=odds, market_respect=self._respect(score=80, line_clv=0.0))

        self.assertEqual(policy["policy_status"], "BLOCKED")
        self.assertIn("medium_edge_requires_market_respect_and_positive_clv", policy["policy_reasons"])

    def test_extreme_edge_can_be_approved_with_market_confirmation(self) -> None:
        edge, game, prediction, odds = self._edge_game(model_total=6.5, book_total=9.0)

        policy = evaluate_totals_policy(self.db, edge=edge, game=game, prediction=prediction, odds=odds, market_respect=self._respect(score=88, line_clv=0.75))

        self.assertEqual(policy["policy_status"], "APPROVED")
        self.assertGreaterEqual(policy["totals_policy_score"], 70)
        self.assertTrue(policy["alert_allowed"])

    def test_approved_total_without_clv_or_market_confirmation_cannot_alert(self) -> None:
        edge, game, prediction, odds = self._edge_game(model_total=6.5, book_total=9.0)

        policy = evaluate_totals_policy(
            self.db,
            edge=edge,
            game=game,
            prediction=prediction,
            odds=odds,
            market_respect=self._respect(score=88, line_clv=0.0, movement_direction="neutral", sharp_match=False),
        )

        self.assertEqual(policy["policy_status"], "APPROVED")
        self.assertFalse(policy["alert_allowed"])
        self.assertIn("alert_suppressed_without_clv_or_market_confirmation", policy["policy_reasons"])

    def test_caution_total_is_research_only_not_alertable(self) -> None:
        edge, game, prediction, odds = self._edge_game(model_total=6.5, book_total=9.0, wind_factor=0.35)

        policy = evaluate_totals_policy(
            self.db,
            edge=edge,
            game=game,
            prediction=prediction,
            odds=odds,
            market_respect=self._respect(score=96, line_clv=0.0, late_move=True),
        )

        self.assertEqual(policy["policy_status"], "CAUTION")
        self.assertTrue(policy["alert_confirmed"])
        self.assertFalse(policy["alert_allowed"])

    def test_explosive_environment_blocks_under(self) -> None:
        edge, game, prediction, odds = self._edge_game(
            model_total=6.5,
            book_total=9.0,
            wind_factor=0.55,
            bullpen_strength=0.3,
            park_factor_adv=0.07,
        )

        policy = evaluate_totals_policy(self.db, edge=edge, game=game, prediction=prediction, odds=odds, market_respect=self._respect(score=88, line_clv=0.75))

        self.assertEqual(policy["policy_status"], "BLOCKED")
        self.assertIn("explosive_run_environment", policy["policy_reasons"])

    def test_under_cluster_risk_penalizes_under_rows(self) -> None:
        rows = [
            {"play": "under", "totals_policy": {"policy_status": "APPROVED", "totals_policy_score": 80, "policy_reasons": []}},
            {"play": "under", "totals_policy": {"policy_status": "APPROVED", "totals_policy_score": 75, "policy_reasons": []}},
            {"play": "under", "totals_policy": {"policy_status": "CAUTION", "totals_policy_score": 55, "policy_reasons": []}},
            {"play": "over", "totals_policy": {"policy_status": "APPROVED", "totals_policy_score": 80, "policy_reasons": []}},
        ]

        cluster = apply_under_cluster_risk(rows)

        self.assertEqual(cluster["warning"], "UNDER CLUSTER RISK")
        self.assertEqual(rows[0]["policy_status"], "CLUSTER_RISK")
        self.assertLess(rows[0]["totals_policy_score"], 80)


if __name__ == "__main__":
    unittest.main()
