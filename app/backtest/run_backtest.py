from __future__ import annotations

import pandas as pd
from sklearn.linear_model import LogisticRegression


def run_backtest(seasons: list[int]):
    data = [
        {"ops_diff": 0.05, "era_diff": -0.5, "home_win": 1},
        {"ops_diff": -0.03, "era_diff": 0.7, "home_win": 0},
        {"ops_diff": 0.02, "era_diff": -0.2, "home_win": 1},
        {"ops_diff": -0.06, "era_diff": 0.9, "home_win": 0},
    ]

    df = pd.DataFrame(data)

    X = df[["ops_diff", "era_diff"]]
    y = df["home_win"]

    model = LogisticRegression()
    model.fit(X, y)

    return {
        "status": "model trained",
        "coefficients": {
            "ops_diff": float(model.coef_[0][0]),
            "era_diff": float(model.coef_[0][1]),
        },
        "intercept": float(model.intercept_[0]),
        "seasons": seasons,
    }
