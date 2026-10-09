"""Past-only conditional negative-binomial totals, with explicit push mass."""
import math
import numpy as np
from scipy.optimize import minimize
from scipy.stats import nbinom, poisson

TOTALS_VERSION = "totals-nb-monthly-v1"
TOTALS_NO_FACTOR_VERSION = "totals-nb-no082-monthly-v1"


def input_mean(projected_total, scheduled_start, seasonal_factor):
    # Stored live projections already contain the pre-June multiplier. Undo it
    # only in the prespecified no-factor ablation, in training and prediction.
    mean = float(projected_total)
    if not seasonal_factor and scheduled_start.month < 6:
        mean /= .82
    return max(.1, mean)


def fit_totals(rows, cutoff, *, seasonal_factor=True):
    unique = {}
    for row in sorted(rows, key=lambda r: (r["created_at"], r["game_id"])):
        if row["created_at"] < cutoff and row["observed_at"] < cutoff and row["scheduled_start"] < cutoff:
            unique.setdefault(row["game_id"], row)
    train = list(unique.values())
    if len(train) < 30:
        return None
    x = np.log([input_mean(r["projected_total"], r["scheduled_start"], seasonal_factor) for r in train])
    y = np.asarray([r["total"] for r in train], dtype=float)
    # Conditional mean: log(mu)=a+b*log(projected total), with fixed weak ridge
    # toward identity. Dispersion is fitted to residuals at each fitted mean.
    def objective(theta):
        eta = np.clip(theta[0]+theta[1]*x, -4, 5)
        return float(np.sum(np.exp(eta)-y*eta)+.5*(theta[0]**2+(theta[1]-1)**2))
    result = minimize(objective, [0., 1.], method="L-BFGS-B", bounds=[(-5, 5), (-2, 3)])
    if not result.success or not np.isfinite(result.fun):
        return None
    mu = np.exp(np.clip(result.x[0]+result.x[1]*x, -4, 5))
    alpha = max(0., float(np.sum((y-mu)**2-mu)/np.sum(mu**2)))
    return dict(intercept=float(result.x[0]), slope=float(result.x[1]), alpha=alpha,
        train_n=len(train), cutoff=cutoff.isoformat(), seasonal_factor=seasonal_factor,
        variance_rule="mu + alpha * mu^2", train_last_observed=max(r["observed_at"] for r in train).isoformat())


def totals_probability(fit, projected_total, line, scheduled_start):
    base = input_mean(projected_total, scheduled_start, fit["seasonal_factor"])
    mu = float(np.exp(np.clip(fit["intercept"]+fit["slope"]*math.log(base), -4, 5)))
    alpha = fit["alpha"]
    distribution = nbinom(1/alpha, 1/(1+alpha*mu)) if alpha > 1e-8 else poisson(mu)
    integer = float(line).is_integer()
    over = float(distribution.sf(math.floor(line)))
    push = float(distribution.pmf(int(line))) if integer else 0.
    under = max(0., 1-over-push)
    conditional = over/(over+under)
    return dict(probability=conditional, over_probability=over, under_probability=under,
        push_probability=push, conditional_mean=mu, conditional_variance=mu+alpha*mu**2)
