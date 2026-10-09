"""Fail-closed, fixed prospective cohort. No historical season data can unlock it."""
from datetime import datetime, timezone
import json
import os

from app.models.schema import GateDeployment, GateMember, ForecastHistory, ForecastGrade, PriceHistory
from app.services.history_service import utc
from app.services.shadow_math import JOINT_VERSION, proper_scores
from app.services.verified_clv_service import verified_clv, clustered_interval

GATE_POLICY = "prospective-300-date-bootstrap95-v1"


def market_for_play(play):
    return "totals" if play in {"over", "under"} else "moneyline" if play in {"home_ml", "away_ml"} else None


def enroll_forecast(db, forecast):
    if forecast.model_version != JOINT_VERSION:
        return
    now, start = utc(forecast.created_at), utc(forecast.scheduled_start)
    deploy = db.query(GateDeployment).filter_by(market=forecast.market).first()
    if deploy is None:
        deploy = GateDeployment(market=forecast.market, candidate_version=JOINT_VERSION, deployed_at=now,
            cohort_size=max(1, int(os.getenv("OOS_GATE_COHORT_SIZE", "300"))),
            horizon_minutes=max(1, int(os.getenv("OOS_GATE_HORIZON_MINUTES", "45"))),
            tolerance_minutes=max(0, int(os.getenv("OOS_GATE_TOLERANCE_MINUTES", "5"))), policy_version=GATE_POLICY)
        db.add(deploy)
        db.flush()
    if forecast.model_version != deploy.candidate_version or now < utc(deploy.deployed_at) or not start or now >= start:
        return
    minutes = (start-now).total_seconds()/60
    if abs(minutes-deploy.horizon_minutes) > deploy.tolerance_minutes:
        return
    if db.query(GateMember).filter_by(deployment_id=deploy.id, game_id=forecast.game_id).first():
        return
    db.add(GateMember(deployment_id=deploy.id, game_id=forecast.game_id, forecast_id=forecast.id, enrolled_at=now))
    db.flush()


def evaluate_cohort(rows, required):
    if len(rows) < required:
        return dict(status="SHADOW", reason="prospective_cohort_incomplete", graded=len(rows), required=required)
    rows = rows[:required]  # Fixed prefix; later results cannot rescue a failed cohort.
    scored = []
    for row in rows:
        candidate, market = proper_scores([row["y"]], [row["candidate_p"]]), proper_scores([row["y"]], [row["market_p"]])
        scored.append({**row, "brier_difference": candidate["brier"]-market["brier"],
                       "log_loss_difference": candidate["log_loss"]-market["log_loss"]})
    dates = len({r["date"] for r in scored})
    diffs = {}
    for key in ("brier_difference", "log_loss_difference"):
        mean, _, upper = clustered_interval(scored, key, confidence=.90)
        # Upper 95th percentile is the one-sided 95% bound, not a two-sided 95% bound.
        diffs[key] = {"mean": mean, "upper95_one_sided": upper}
    verified = [r for r in scored if r.get("verification") == "verified_same_book"]
    clv = sum(r["probability_clv"] for r in verified)/len(verified) if verified else None
    passed = dates >= 10 and all(v["upper95_one_sided"] is not None and v["upper95_one_sided"] < 0 for v in diffs.values()) \
             and len(verified) == required and clv is not None and clv > 0
    return dict(status="PASS" if passed else "SHADOW", reason="fixed_cohort_passed" if passed else "skill_or_verified_clv_not_established",
        graded=len(rows), required=required, clustered_dates=dates, differences=diffs,
        mean_verified_clv=clv, verified_clv_coverage=len(verified)/required,
        method="fixed prefix; date-cluster bootstrap 3000 draws; both one-sided 95% upper bounds <0; full verified CLV coverage")


def gate_status(db, market, *, as_of=None):
    now = utc(as_of) or datetime.now(timezone.utc)
    deploy = db.query(GateDeployment).filter_by(market=market).first()
    if not deploy or utc(deploy.deployed_at) > now:
        return dict(market=market, status="SHADOW", reason="not_deployed", required=300, graded=0)
    members = db.query(GateMember, ForecastHistory).join(ForecastHistory,
        ForecastHistory.id == GateMember.forecast_id).filter(GateMember.deployment_id == deploy.id,
        GateMember.enrolled_at <= now).order_by(GateMember.enrolled_at, GateMember.id).all()
    rows, pushes = [], 0
    for member, forecast in members:
        grade = db.query(ForecastGrade).filter_by(forecast_id=forecast.id).first()
        # Never skip unresolved early games and thereby select quick/winning outcomes.
        if grade is None or utc(grade.observed_at) > now:
            break
        if grade.push:
            pushes += 1  # Prespecified replacement by the next enrolled game, without binary grading pushes.
            continue
        if forecast.model_version != deploy.candidate_version or utc(forecast.created_at) < utc(deploy.deployed_at):
            break
        outputs = json.loads(forecast.outputs_json)
        rows.append(dict(date=utc(forecast.scheduled_start).date().isoformat(), y=grade.y,
            candidate_p=outputs["probability"], market_p=json.loads(forecast.inputs_json)["market_p"],
            **verified_clv(db, entry=db.get(PriceHistory, forecast.price_id), play=outputs["play"],
                decided_at=forecast.created_at, scheduled_start=forecast.scheduled_start, as_of=now)))
        if len(rows) == deploy.cohort_size:
            break
    return dict(market=market, candidate_version=deploy.candidate_version,
        deployed_at=utc(deploy.deployed_at).isoformat(), enrolled=len(members), pushes=pushes,
        horizon_minutes=deploy.horizon_minutes, policy_version=deploy.policy_version,
        **evaluate_cohort(rows, deploy.cohort_size))


def gate_for_play(db, play, *, as_of=None):
    return gate_status(db, market_for_play(play), as_of=as_of)


def gate_report(db):
    return [gate_status(db, market) for market in ("moneyline", "totals")]


def gate_allows_action(db, *, game_id, play, action, sportsbook=None, line=None):
    status = gate_for_play(db, play)
    if status["status"] == "PASS":
        return True
    from app.services.history_service import append_decision
    append_decision(db, dict(game_id=game_id, play=play, decision_status="SHADOW",
        reason="prospective_gate_has_not_passed", action=action, sportsbook=sportsbook, line=line,
        prospective_gate=status))
    db.commit()
    return False
