from __future__ import annotations

import unittest

from app.services.feature_builder import build_team_features


class FeatureBuilderTests(unittest.TestCase):
    def _raw_stats(self) -> dict:
        return {
            "games_played": 80,
            "runs": 360,
            "runs_allowed": 340,
            "whip": 1.28,
            "avg": 0.250,
            "ops": 0.725,
            "home_runs": 90,
        }

    def test_starter_run_prevention_falls_back_to_era_when_xera_missing(self) -> None:
        features = build_team_features(
            self._raw_stats(),
            starter_stats={"era": 3.82, "whip": 1.11, "kbb": 5.5},
        )

        self.assertEqual(features["starter_run_prevention"], 3.82)
        self.assertFalse(features["using_xera"])

    def test_starter_run_prevention_prefers_xera(self) -> None:
        features = build_team_features(
            self._raw_stats(),
            starter_stats={"era": 4.20, "xera": 3.33, "whip": 1.11, "kbb": 5.5},
        )

        self.assertEqual(features["starter_run_prevention"], 3.33)
        self.assertTrue(features["using_xera"])
        self.assertEqual(features["starter_xera"], 3.33)


if __name__ == "__main__":
    unittest.main()
