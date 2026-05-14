from __future__ import annotations

import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.schema import GameOutcomeReview, SandboxPredictionV4
from app.services.backtest_service import _v05_signal_correlations


class BacktestAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        testing_session_local = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = testing_session_local()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_v05_signal_correlations_flag_candidates_above_threshold(self) -> None:
        rows = [
            (1, 0.80, 1, 5, 3),
            (2, 0.70, 1, 6, 2),
            (3, 0.10, 3, 2, 5),
            (4, 0.20, 3, 1, 4),
        ]
        for game_id, travel_stress, series_game_number, home_score, away_score in rows:
            self.db.add(
                SandboxPredictionV4(
                    game_id=game_id,
                    game_date=date(2026, 5, game_id),
                    season=2026,
                    travel_stress_away=travel_stress,
                    travel_stress_home=0.0,
                    series_game_number=series_game_number,
                    is_series_opener=series_game_number == 1,
                    is_series_finale=series_game_number == 3,
                )
            )
            self.db.add(
                GameOutcomeReview(
                    game_id=game_id,
                    prediction_id=game_id,
                    game_date=date(2026, 5, game_id),
                    actual_outcome_summary="final",
                    final_home_score=home_score,
                    final_away_score=away_score,
                    winning_side="home" if home_score > away_score else "away",
                    bet_result="win",
                    was_model_correct=home_score > away_score,
                )
            )
        self.db.commit()

        report = _v05_signal_correlations(self.db, [2026])

        self.assertEqual(report["n_games"], 4)
        by_signal = {row["signal"]: row for row in report["signals"]}
        self.assertGreater(by_signal["travel_stress"]["abs_r"], 0.04)
        self.assertTrue(by_signal["travel_stress"]["candidate_for_feature_set"])
        self.assertGreater(by_signal["series_game_number"]["abs_r"], 0.04)
        self.assertTrue(by_signal["series_game_number"]["candidate_for_feature_set"])


if __name__ == "__main__":
    unittest.main()
