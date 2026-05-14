from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.schema import Game, SandboxPredictionV4
from app.services.sandbox_grade_service import (
    _first_five_total_from_linescore,
    grade_f5_result,
    grade_sandbox_f5_predictions,
)


class SandboxGradeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        testing_session_local = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = testing_session_local()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_first_five_total_from_linescore(self) -> None:
        payload = {
            "innings": [
                {"num": 1, "away": {"runs": 1}, "home": {"runs": 0}},
                {"num": 2, "away": {"runs": 0}, "home": {"runs": 1}},
                {"num": 3, "away": {"runs": 2}, "home": {"runs": 0}},
                {"num": 4, "away": {"runs": 0}, "home": {"runs": 0}},
                {"num": 5, "away": {"runs": 1}, "home": {"runs": 2}},
                {"num": 6, "away": {"runs": 9}, "home": {"runs": 9}},
            ]
        }

        self.assertEqual(_first_five_total_from_linescore(payload), 7)

    def test_grade_f5_result_handles_win_loss_push(self) -> None:
        self.assertEqual(grade_f5_result("OVER", 5, 4.5), "WIN")
        self.assertEqual(grade_f5_result("OVER", 4, 4.5), "LOSS")
        self.assertEqual(grade_f5_result("UNDER", 4, 4.5), "WIN")
        self.assertEqual(grade_f5_result("UNDER", 5, 4.5), "LOSS")
        self.assertEqual(grade_f5_result("OVER", 5, 5.0), "PUSH")

    @patch("app.services.sandbox_grade_service.requests.get")
    def test_grade_sandbox_f5_predictions_uses_mlb_linescore(self, get_mock: Mock) -> None:
        game = Game(
            game_id=123,
            game_date=date(2026, 5, 14),
            season=2026,
            away_team="Away",
            home_team="Home",
            final_away_score=6,
            final_home_score=4,
        )
        prediction = SandboxPredictionV4(
            game_id=123,
            game_date=game.game_date,
            season=2026,
            away_team="Away",
            home_team="Home",
            f5_projected_total=5.2,
            f5_line=4.5,
            f5_pick="OVER",
            full_game_projected_total=9.5,
            v3_projected_total=8.5,
        )
        self.db.add_all([game, prediction])
        self.db.commit()

        response = Mock()
        response.json.return_value = {
            "innings": [
                {"num": 1, "away": {"runs": 0}, "home": {"runs": 0}},
                {"num": 2, "away": {"runs": 1}, "home": {"runs": 0}},
                {"num": 3, "away": {"runs": 2}, "home": {"runs": 1}},
                {"num": 4, "away": {"runs": 0}, "home": {"runs": 0}},
                {"num": 5, "away": {"runs": 0}, "home": {"runs": 1}},
            ]
        }
        response.raise_for_status.return_value = None
        get_mock.return_value = response

        result = grade_sandbox_f5_predictions(self.db)

        self.assertEqual(result["graded"], 1)
        self.assertEqual(result["skipped"], 0)
        self.db.refresh(prediction)
        self.assertEqual(prediction.f5_result, "WIN")
        self.assertIsNotNone(prediction.f5_graded_at)
        self.assertEqual(prediction.full_game_result, "WIN")


if __name__ == "__main__":
    unittest.main()
