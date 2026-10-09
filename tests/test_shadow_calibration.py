import json
from datetime import timedelta
import numpy as np

from test_improvement_history import db, NOW
from test_verified_clv import quote
from app.models.schema import ForecastHistory, ForecastGrade, Game, ShadowFit
from app.services.history_service import append_forecast
from app.services.shadow_math import fit_joint, joint_probability, JOINT_VERSION
from app.services.shadow_service import produce_shadow_forecasts, grade_shadow_forecasts, shadow_score_report


def test_future_outcomes_and_repeated_games_cannot_train_fit():
    rows = [dict(game_id=i, created_at=NOW-timedelta(days=50), observed_at=NOW-timedelta(days=49),
        scheduled_start=NOW-timedelta(days=50), model_p=.1+.8*(i%2), market_p=.4+.2*(i%2), y=i%2)
        for i in range(40)]
    future = [{**r, "game_id":r["game_id"]+100, "observed_at":NOW+timedelta(days=1)} for r in rows]
    first = fit_joint(rows+future, NOW)
    for r in future:
        r["y"] = 1-r["y"]
    assert first == fit_joint(rows+future, NOW)
    assert first == fit_joint(rows+rows, NOW)
    assert first["coefficients"][1] == 0  # No inner chronological cohort; market-only is valid.
    startup = fit_joint([], NOW)
    assert joint_probability(startup, .6, .99) == .6


def test_shadow_runs_beside_frozen_live_forecast_and_grades_idempotently(db):
    base = append_forecast(db, game_id=1, model_version="live-v1", calibration_version="none",
        created_at=NOW-timedelta(minutes=1), inputs={"x":1},
        outputs={"home_win_pct":.9, "projected_total":9.})
    db.commit()
    quote(db, time=NOW)
    quote(db, time=NOW, market="totals", line=8)
    original = base.outputs_json
    rows = produce_shadow_forecasts(db, 1, as_of=NOW)
    db.commit()
    assert len(rows) == 2 and base.outputs_json == original
    for row in rows:
        result = json.loads(row.outputs_json)
        assert result["shadow_only"] and result["probability"] == result["market_probability"]
        assert row.price_id and row.calibration_version.startswith("fit:")
    game = db.get(Game, 1)
    game.status, game.final_home_score, game.final_away_score = "Final", 5, 3
    db.commit()
    assert grade_shadow_forecasts(db, as_of=NOW)["graded"] == 0
    assert grade_shadow_forecasts(db, as_of=NOW+timedelta(hours=4))["graded"] == 2
    assert grade_shadow_forecasts(db, as_of=NOW+timedelta(hours=4))["graded"] == 0
    assert sum(g.push for g in db.query(ForecastGrade)) == 1
    assert len(shadow_score_report(db)) == 2
    # Monthly fits stay frozen even when later outcomes become available.
    before = [f.payload_json for f in db.query(ShadowFit).order_by(ShadowFit.id)]
    produce_shadow_forecasts(db, 1, as_of=NOW+timedelta(minutes=1))
    assert before == [f.payload_json for f in db.query(ShadowFit).order_by(ShadowFit.id)]
    assert produce_shadow_forecasts(db, 1, as_of=NOW+timedelta(hours=3)) == []


def test_future_or_stale_prices_do_not_produce_shadow(db):
    append_forecast(db, game_id=1, model_version="live-v1", calibration_version="none", created_at=NOW,
                    inputs={}, outputs={"home_win_pct":.7, "projected_total":8.})
    db.commit()
    quote(db, time=NOW+timedelta(minutes=1))
    assert produce_shadow_forecasts(db, 1, as_of=NOW) == []
    assert produce_shadow_forecasts(db, 1, as_of=NOW+timedelta(minutes=20)) == []
