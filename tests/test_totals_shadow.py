import json
from datetime import timedelta
import pytest

from test_improvement_history import db, NOW
from test_verified_clv import quote
from app.models.schema import ForecastHistory, ForecastGrade
from app.services.history_service import append_forecast
from app.services.shadow_math import JOINT_VERSION
from app.services.shadow_service import produce_shadow_forecasts, grade_shadow_forecasts
from app.services.totals_shadow_math import fit_totals, totals_probability, input_mean, TOTALS_VERSION, TOTALS_NO_FACTOR_VERSION
from analysis.totals_shadow_walkforward import walk_forward_totals


def training():
    start = NOW.replace(month=8, day=10)
    return [dict(game_id=i+10, created_at=start-timedelta(hours=1), scheduled_start=start,
        observed_at=start+timedelta(hours=8), projected_total=7.+i%5, total=3+((i*7)%16)) for i in range(40)]


def test_fit_uses_conditional_residuals_and_earlier_observed_unique_games_only():
    rows = training()
    fit = fit_totals(rows, NOW)
    assert fit["train_n"] == 40 and fit["alpha"] >= 0
    assert fit_totals(rows+rows, NOW) == fit
    future = [{**r, "game_id":r["game_id"]+100, "observed_at":NOW+timedelta(days=1), "total":100} for r in rows]
    assert fit_totals(rows+future, NOW) == fit
    assert fit_totals(rows[:29], NOW) is None
    low = totals_probability(fit, 6., 8., NOW)
    high = totals_probability(fit, 12., 8., NOW)
    assert low["conditional_mean"] != high["conditional_mean"]
    assert low["conditional_variance"] == pytest.approx(low["conditional_mean"]+fit["alpha"]*low["conditional_mean"]**2)


def test_integer_push_mass_and_seasonal_ablation():
    fit = dict(intercept=0., slope=1., alpha=.1, seasonal_factor=True)
    integer = totals_probability(fit, 8., 8., NOW)
    half = totals_probability(fit, 8., 8.5, NOW)
    assert integer["push_probability"] > 0 and half["push_probability"] == 0
    assert sum(integer[k] for k in ("over_probability", "under_probability", "push_probability")) == pytest.approx(1)
    assert integer["probability"] == pytest.approx(integer["over_probability"]/(1-integer["push_probability"]))
    april = NOW.replace(month=4)
    assert input_mean(8.2, april, False) == pytest.approx(10.)
    assert input_mean(8.2, NOW, False) == 8.2
    assert input_mean(8.2, april, True) == 8.2
    # The existing live distribution remains untouched.
    from app.services.edge_service import TOTAL_STD_DEV
    assert TOTAL_STD_DEV == 2.5


def test_walkforward_cannot_learn_next_month_outcomes():
    rows = training()
    for row in rows:
        row["month"] = "2026-08"
    test = [{**r, "game_id":r["game_id"]+100, "created_at":NOW-timedelta(hours=1),
        "scheduled_start":NOW, "observed_at":NOW+timedelta(hours=8), "month":"2026-10", "line":8.} for r in rows]
    original, fits = walk_forward_totals(rows+test)
    changed, changed_fits = walk_forward_totals(rows+[{**r, "total":100-r["total"]} for r in test])
    assert len(original) == 40 and fits == changed_fits
    assert [r["nb_factor"] for r in original] == [r["nb_factor"] for r in changed]
    assert all(r["train_n"] == 40 for r in original)


def test_both_totals_candidates_use_shadow_history_and_earlier_grades(db):
    for r in training():
        f = append_forecast(db, game_id=r["game_id"], model_version=JOINT_VERSION, calibration_version="old-fit",
            created_at=r["created_at"], market="totals", line=8.,
            inputs={"market_p":.5, "model_p":.6, "projected_total":r["projected_total"]}, outputs={"probability":.6})
        f.scheduled_start = r["scheduled_start"]  # Set before the first insert, never update history.
        db.flush()
        db.add(ForecastGrade(forecast_id=f.id, observed_at=r["observed_at"], home_score=r["total"], away_score=0,
                             y=int(r["total"]>8), push=r["total"]==8, brier=.25, log_loss=.69))
    append_forecast(db, game_id=1, model_version="live-v1", calibration_version="none", created_at=NOW,
                    inputs={}, outputs={"home_win_pct":.6, "projected_total":9.})
    db.commit()
    quote(db, time=NOW, market="totals", line=8.)
    rows = produce_shadow_forecasts(db, 1, as_of=NOW)
    db.commit()
    candidates = [r for r in rows if r.model_version in {TOTALS_VERSION, TOTALS_NO_FACTOR_VERSION}]
    assert len(candidates) == 2
    for row in candidates:
        output = json.loads(row.outputs_json)
        assert row.price_id and output["shadow_only"] and output["push_probability"] > 0
