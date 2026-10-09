"""Pure, credential-free shadow estimators; frozen chronological fitting rules."""
from datetime import timedelta
import numpy as np
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression

JOINT_VERSION = "joint-monthly-c1-v1"


def proper_scores(y, p):
    y, p = np.asarray(y), np.clip(np.asarray(p), 1e-6, 1-1e-6)
    return {"brier": float(np.mean((p-y)**2)),
            "log_loss": float(-np.mean(y*np.log(p)+(1-y)*np.log1p(-p)))}


def joint_probability(fit, market_p, model_p):
    x = logit(np.clip([market_p, model_p], 1e-6, 1-1e-6))
    return float(expit(fit["intercept"]+np.dot(fit["coefficients"], x)))


def fit_joint(rows, cutoff):
    """Earlier observed outcomes only; inner chronological validation permits zero model weight.

    Fixed L2 C=1. Select joint vs fitted-market on the last earlier month using
    log loss AND Brier; refit the selection on all earlier observations. Startup
    is the raw market (model weight exactly zero), not a fitted guess.
    """
    eligible = [r for r in rows if r["created_at"] < cutoff and r["observed_at"] < cutoff
                and r["scheduled_start"] < cutoff and r["y"] in (0, 1)]
    unique = {}
    for row in sorted(eligible, key=lambda r: (r["created_at"], r["game_id"])):
        unique.setdefault(row["game_id"], row)
    train = list(unique.values())
    fallback = dict(intercept=0., coefficients=[1., 0.], train_n=len(train),
                    train_cutoff=cutoff.isoformat(), selection="raw_market_startup")
    if len(train) < 30 or len({r["y"] for r in train}) < 2:
        return fallback
    def fitted(data, joint):
        x = logit(np.clip([[r["market_p"], r["model_p"]] for r in data], 1e-6, 1-1e-6))
        fit = LogisticRegression(C=1., solver="lbfgs", max_iter=2000).fit(x if joint else x[:, :1], [r["y"] for r in data])
        return dict(intercept=float(fit.intercept_[0]), coefficients=[float(fit.coef_[0, 0]),
            float(fit.coef_[0, 1]) if joint else 0.])
    # The validation block was itself untouched by the inner fit, with one-day embargo.
    last_month = max(r["scheduled_start"] for r in train).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    inner = [r for r in train if r["scheduled_start"] < last_month-timedelta(days=1)
             and r["observed_at"] < last_month-timedelta(days=1)]
    valid = [r for r in train if r["scheduled_start"] >= last_month]
    use_joint = False
    if len(inner) >= 30 and len(valid) >= 10 and len({r["y"] for r in inner}) == 2:
        candidates = [fitted(inner, use) for use in (False, True)]
        scores = [proper_scores([r["y"] for r in valid],
                    [joint_probability(c, r["market_p"], r["model_p"]) for r in valid]) for c in candidates]
        use_joint = all(scores[1][key] < scores[0][key] for key in ("brier", "log_loss"))
    return {**fitted(train, use_joint), "train_n": len(train), "train_cutoff": cutoff.isoformat(),
            "selection": "joint" if use_joint else "market_only_zero_model_weight"}
