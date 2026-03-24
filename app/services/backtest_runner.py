"""
Logistic regression on historical game outcomes.

Features used:
  Differential features (home - away):
    - starter_era_diff     : home_starter_era - away_starter_era
    - team_era_diff        : home_team_era    - away_team_era
    - ops_diff             : home_team_ops    - away_team_ops
    - win_pct_diff         : home_win_pct     - away_win_pct
    - run_diff_diff        : home_run_diff    - away_run_diff

  Absolute features:
    - home_starter_era
    - away_starter_era
    - home_team_era
    - away_team_era
    - home_team_ops
    - away_team_ops
    - home_win_pct
    - away_win_pct
    - home_run_diff
    - away_run_diff

Outcome: home_win (1 = home team won, 0 = away team won)

The coefficients are the primary output. A positive coefficient on a feature
means it correlates with the HOME team winning. A negative coefficient means
it correlates with the AWAY team winning.
"""

import json

from sqlalchemy.orm import Session

from app.models.schema import BacktestGame, BacktestResult

FEATURES = [
    # Differential features — most interpretable
    "starter_era_diff",
    "team_era_diff",
    "ops_diff",
    "win_pct_diff",
    "run_diff_diff",
    # Absolute features — capture non-linear effects
    "home_starter_era",
    "away_starter_era",
    "home_team_era",
    "away_team_era",
    "home_team_ops",
    "away_team_ops",
    "home_win_pct",
    "away_win_pct",
    "home_run_diff",
    "away_run_diff",
]


def _build_row(g: BacktestGame) -> list[float]:
    h_se  = g.home_starter_era  or 4.50
    a_se  = g.away_starter_era  or 4.50
    h_te  = g.home_team_era     or 4.20
    a_te  = g.away_team_era     or 4.20
    h_ops = g.home_team_ops     or 0.720
    a_ops = g.away_team_ops     or 0.720
    h_wp  = g.home_win_pct      or 0.5
    a_wp  = g.away_win_pct      or 0.5
    h_rd  = g.home_run_diff     or 0
    a_rd  = g.away_run_diff     or 0

    return [
        h_se - a_se,        # starter_era_diff   (lower = home starter better)
        h_te - a_te,        # team_era_diff
        h_ops - a_ops,      # ops_diff           (higher = home offense better)
        h_wp - a_wp,        # win_pct_diff
        h_rd - a_rd,        # run_diff_diff
        h_se, a_se,
        h_te, a_te,
        h_ops, a_ops,
        h_wp, a_wp,
        float(h_rd), float(a_rd),
    ]


def run_regression(db: Session, seasons: list[int]) -> dict:
    """
    Run logistic regression on BacktestGame records for the given seasons.
    Persists a BacktestResult and returns the full result dict.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import log_loss
    from sklearn.model_selection import cross_val_score, train_test_split
    from sklearn.preprocessing import StandardScaler

    games = (
        db.query(BacktestGame)
        .filter(BacktestGame.season.in_(seasons))
        .filter(BacktestGame.home_win.isnot(None))
        .all()
    )

    if len(games) < 100:
        return {"error": f"Only {len(games)} games found — need at least 100 to run regression"}

    X = [_build_row(g) for g in games]
    y = [1 if g.home_win else 0 for g in games]

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # 5-fold cross-validation accuracy
    model = LogisticRegression(max_iter=1000, random_state=42)
    cv_scores = cross_val_score(model, X_scaled, y, cv=5, scoring="accuracy")
    cv_accuracy = float(cv_scores.mean())

    # Train/test split for held-out accuracy + log loss
    X_train, X_test, y_train, y_test = train_test_split(
        X_scaled, y, test_size=0.2, random_state=42
    )
    model.fit(X_train, y_train)
    test_accuracy = float(model.score(X_test, y_test))
    ll = float(log_loss(y_test, model.predict_proba(X_test)))

    # Coefficients — positive = helps home team, negative = helps away team
    coefs = dict(zip(FEATURES, [round(float(c), 4) for c in model.coef_[0]]))

    # Rank features by absolute coefficient value
    ranked = sorted(coefs.items(), key=lambda x: abs(x[1]), reverse=True)

    # Baseline: home teams win ~54% in MLB
    home_win_rate = sum(y) / len(y)

    result_data = {
        "seasons":       seasons,
        "n_games":       len(games),
        "home_win_rate": round(home_win_rate, 4),
        "test_accuracy": round(test_accuracy, 4),
        "cv_accuracy":   round(cv_accuracy, 4),
        "log_loss":      round(ll, 4),
        "coefficients":  coefs,
        "feature_ranks": [
            {
                "feature":     f,
                "coefficient": c,
                "interpretation": _interpret(f, c),
            }
            for f, c in ranked
        ],
    }

    # Persist
    br = BacktestResult(
        seasons              = ",".join(str(s) for s in seasons),
        n_games              = len(games),
        accuracy             = test_accuracy,
        cv_accuracy          = cv_accuracy,
        log_loss             = ll,
        coefficients_json    = json.dumps(coefs),
        feature_ranks_json   = json.dumps([r["feature"] for r in ranked]),
    )
    db.add(br)
    db.commit()
    db.refresh(br)

    result_data["result_id"] = br.id
    return result_data


def _interpret(feature: str, coef: float) -> str:
    """Plain-English interpretation of a coefficient."""
    direction = "home" if coef > 0 else "away"
    strength  = "strong" if abs(coef) > 0.3 else "moderate" if abs(coef) > 0.1 else "weak"

    labels = {
        "starter_era_diff": f"{strength} signal — lower home starter ERA vs away → favors {direction}",
        "team_era_diff":    f"{strength} signal — home team pitching depth → favors {direction}",
        "ops_diff":         f"{strength} signal — home team offense vs away → favors {direction}",
        "win_pct_diff":     f"{strength} signal — home team W/L record → favors {direction}",
        "run_diff_diff":    f"{strength} signal — home run differential → favors {direction}",
        "home_starter_era": f"{strength} signal — home starter ERA (absolute) → favors {direction}",
        "away_starter_era": f"{strength} signal — away starter ERA (absolute) → favors {direction}",
        "home_team_era":    f"{strength} signal — home team bullpen/rotation → favors {direction}",
        "away_team_era":    f"{strength} signal — away team bullpen/rotation → favors {direction}",
        "home_team_ops":    f"{strength} signal — home lineup quality → favors {direction}",
        "away_team_ops":    f"{strength} signal — away lineup quality → favors {direction}",
        "home_win_pct":     f"{strength} signal — home team record → favors {direction}",
        "away_win_pct":     f"{strength} signal — away team record → favors {direction}",
        "home_run_diff":    f"{strength} signal — home run differential → favors {direction}",
        "away_run_diff":    f"{strength} signal — away run differential → favors {direction}",
    }
    return labels.get(feature, f"{strength} signal → favors {direction}")
