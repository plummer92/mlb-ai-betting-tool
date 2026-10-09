import json
from datetime import timedelta
from unittest.mock import patch

from test_improvement_history import db, NOW
from test_verified_clv import quote
from app.models.schema import ForecastGrade, ForecastHistory, GateDeployment, GateMember, PaperTrade, BetAlert
from app.services.history_service import append_forecast
from app.services.shadow_math import JOINT_VERSION
from app.services.prospective_gate_service import evaluate_cohort, enroll_forecast, gate_status, gate_allows_action
from app.services.market_respect_service import classify_tradable_signal
from app.services.paper_trade_service import log_alert_as_paper_trade


def cohort(n=300):
    return [dict(date=f"2026-09-{1+i//15:02d}", y=i%2, candidate_p=.9 if i%2 else .1,
        market_p=.5, verification="verified_same_book", probability_clv=.01) for i in range(n)]


def test_gate_requires_fixed_sample_both_proper_scores_date_clusters_and_verified_clv():
    assert evaluate_cohort(cohort(299), 300)["status"] == "SHADOW"
    rows = cohort()
    assert evaluate_cohort(rows, 300)["status"] == "PASS"
    assert evaluate_cohort([{**r, "candidate_p":.5} for r in rows], 300)["status"] == "SHADOW"
    assert evaluate_cohort([{**r, "date":"one"} for r in rows], 300)["status"] == "SHADOW"
    assert evaluate_cohort([{**r, "probability_clv":-.01} for r in rows], 300)["status"] == "SHADOW"
    assert evaluate_cohort([{**r, "verification":"fallback"} for r in rows], 300)["status"] == "SHADOW"
    bad = [{**r, "candidate_p":.1 if r["y"] else .9} for r in rows]
    assert evaluate_cohort(bad+rows, 300)["status"] == "SHADOW"  # No optional stopping / later rescue.


def test_no_gate_never_loosens_current_blocked_or_watch_decisions():
    cases = [dict(score=25, tags=["MARKET REJECTED"], components={"price_clv":.05}),
             dict(score=50, tags=["MARKET NEUTRAL"], components={})]
    for respect in cases:
        args = dict(market_adjustment={"adjusted_ev":.1, "adjusted_edge_pct":.08,
            "adjusted_confidence":"strong"}, play="home_ml", raw_edge_pct=.08, raw_ev=.1)
        old = classify_tradable_signal(market_respect=respect, **args)
        new = classify_tradable_signal(market_respect={**respect, "prospective_gate":{"status":"SHADOW"}}, **args)
        assert old["trade_allowed"] is False and new["trade_allowed"] is False
        assert old["tradable_signal"] == new["tradable_signal"]
    respect = dict(score=80, tags=["MARKET AGREED"], components={"price_clv":.01})
    assert classify_tradable_signal(market_respect={**respect, "prospective_gate":{"status":"SHADOW"}}, **args)["tradable_signal"] == "SHADOW"
    assert classify_tradable_signal(market_respect={**respect, "prospective_gate":{"status":"PASS"}}, **args)["tradable_signal"] == "TRADE"


def test_enrollment_fixed_horizon_post_deployment_no_duplicate_and_no_future_grade(db):
    # Fixed 45-minute horizon, matching the pregame job; game start is NOW+2h.
    time = NOW+timedelta(minutes=75)
    entry = quote(db, time=time)
    f = append_forecast(db, game_id=1, model_version=JOINT_VERSION, calibration_version="fit:1",
        created_at=time, market="moneyline", sportsbook="a", price_id=entry.id,
        inputs={"market_p":.5}, outputs={"probability":.6, "play":"home_ml"})
    db.flush()
    enroll_forecast(db, f)
    enroll_forecast(db, f)
    db.commit()
    assert db.query(GateMember).count() == 1
    assert db.query(GateDeployment).first().cohort_size == 300
    assert gate_status(db, "moneyline", as_of=NOW)["reason"] == "not_deployed"
    assert gate_status(db, "moneyline", as_of=time)["graded"] == 0
    db.add(ForecastGrade(forecast_id=f.id, observed_at=NOW+timedelta(hours=4), home_score=5, away_score=3,
                         y=1, push=False, brier=.16, log_loss=.51))
    db.commit()
    assert gate_status(db, "moneyline", as_of=NOW+timedelta(hours=3))["graded"] == 0
    assert gate_status(db, "moneyline", as_of=NOW+timedelta(hours=5))["graded"] == 1
    # Once deployed, later environment changes cannot shrink the fixed cohort.
    with patch.dict("os.environ", {"OOS_GATE_COHORT_SIZE":"1"}):
        enroll_forecast(db, f)
    assert db.query(GateDeployment).first().cohort_size == 300


def test_action_boundary_and_paper_mirror_fail_closed_without_gate(db):
    assert not gate_allows_action(db, game_id=1, play="home_ml", action="test_alert")
    with patch("app.services.paper_trade_service._recorded_terms") as terms:
        log_alert_as_paper_trade(db, BetAlert(game_id=1, play="home_ml"))
        terms.assert_not_called()
    assert db.query(PaperTrade).count() == 0


def test_cached_trade_and_prediction_digest_cannot_bypass_gate(db):
    from app.routes.ranked import _gate_cached_rows
    from app.services.prediction_digest_service import send_daily_prediction_digest
    cached = dict(game_id=1, play="home_ml", tradable_signal="TRADE", trade_allowed=True, decision_status="FIRE")
    checked = _gate_cached_rows(db, [cached])[0]
    assert checked["tradable_signal"] == "SHADOW" and checked["trade_allowed"] is False
    assert cached["trade_allowed"] is True  # Do not mutate the saved report.
    with patch("app.services.prediction_digest_service._build_rows", return_value=[dict(game_id=1, home_win_pct=.6, away_win_pct=.4)]), \
         patch("app.services.prediction_digest_service.send_alert_message") as send:
        result = send_daily_prediction_digest(db, target_date=NOW.date())
    assert result["sent"] == 0 and result["reason"] == "prospective_gate_shadow"
    send.assert_not_called()


def test_real_history_gate_passes_only_after_fixed_cohort_and_first_pitch(db):
    from app.models.schema import PriceHistory
    begin = NOW.replace(month=9, day=1, hour=0)
    deploy = GateDeployment(market="moneyline", candidate_version=JOINT_VERSION, deployed_at=begin,
        cohort_size=20, horizon_minutes=45, tolerance_minutes=5, policy_version="fixture-policy")
    db.add(deploy)
    db.flush()
    for i in range(20):
        start = begin+timedelta(days=i, hours=19)
        entry = PriceHistory(game_id=i+10, sportsbook="a", market="moneyline", first_price=-110, second_price=-110,
            fetched_at=start-timedelta(minutes=50), scheduled_start=start)
        close = PriceHistory(game_id=i+10, sportsbook="a", market="moneyline", first_price=-150 if i%2 else 130,
            second_price=130 if i%2 else -150, fetched_at=start-timedelta(minutes=1), scheduled_start=start)
        db.add_all([entry, close])
        db.flush()
        f = ForecastHistory(game_id=i+10, created_at=start-timedelta(minutes=45), scheduled_start=start,
            model_version=JOINT_VERSION, calibration_version="fixture-fit", feature_hash="x", market="moneyline",
            price_id=entry.id, sportsbook="a", inputs_json=json.dumps({"market_p":.5}),
            outputs_json=json.dumps({"probability":.9 if i%2 else .1, "play":"home_ml" if i%2 else "away_ml"}))
        db.add(f)
        db.flush()
        db.add(GateMember(deployment_id=deploy.id, forecast_id=f.id, game_id=f.game_id, enrolled_at=f.created_at))
        db.add(ForecastGrade(forecast_id=f.id, observed_at=start+timedelta(hours=6), home_score=5 if i%2 else 3,
            away_score=3 if i%2 else 5, y=i%2, push=False, brier=.01, log_loss=.105))
    db.commit()
    assert gate_status(db, "moneyline", as_of=begin+timedelta(days=19, hours=23))["status"] == "SHADOW"
    passed = gate_status(db, "moneyline", as_of=NOW)
    assert passed["status"] == "PASS" and passed["verified_clv_coverage"] == 1
