import numpy as np
import pandas as pd
import pytest

from analysis.season_calibration import no_vig, realized, roi_interval, scores, walk_forward


def test_known_probabilities_and_betting_returns():
    assert no_vig(-110, -110) == .5
    assert no_vig(-150, 150) == pytest.approx(.6)
    assert scores([0, 1], [.5, .5])["brier"] == .25
    assert scores([0, 1], [.5, .5])["log_loss"] == pytest.approx(np.log(2))
    assert realized("under", 4, 5, 9, -110) == ("push", 0)
    assert realized("home_ml", 1, 5, np.nan, -125) == ("win", .8)


def test_missing_price_is_excluded_for_wins_and_losses():
    frame = pd.DataFrame({"game_id": [1, 2, 3], "return": [1, np.nan, np.nan], "stake": [10, 10, 10]})
    result = roi_interval(frame)
    assert result["n"] == 1 and result["unpriced"] == 2 and result["roi"] == 1


def test_walk_forward_cannot_learn_test_month_outcomes():
    n = 40
    frame = pd.DataFrame({"game_id": range(2 * n),
        "game_date": ["2026-03-10"] * n + ["2026-04-10"] * n,
        "month": ["2026-03"] * n + ["2026-04"] * n, "market": "moneyline",
        "model_p": np.tile(np.linspace(.3, .7, n), 2), "market_p": np.tile(np.linspace(.4, .6, n), 2),
        "y": np.tile([0, 1], n)})
    original, fit = walk_forward(frame, "test")
    frame.loc[frame.month == "2026-04", "y"] = 1 - frame.loc[frame.month == "2026-04", "y"]
    changed, changed_fit = walk_forward(frame, "test")
    np.testing.assert_allclose(original.joint, changed.joint)
    pd.testing.assert_frame_equal(fit, changed_fit)
    assert original.train_last_date.eq("2026-03-10").all()
    assert original.month.eq("2026-04").all()
