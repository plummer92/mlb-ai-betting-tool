"""Monthly, past-only shadow fits and grading. No live probability mutation."""
from datetime import datetime, timedelta, timezone
import json
import math

from scipy.stats import norm

from app.models.schema import ForecastHistory, ForecastGrade, ShadowFit, PriceHistory, Game, GameOutcomeReview
from app.services.history_service import append_forecast, forecast_as_of, price_as_of, utc, dumps
from app.services.shadow_math import JOINT_VERSION, fit_joint, joint_probability, proper_scores
from app.services.verified_clv_service import no_vig


def training_rows(db, market, cutoff, *, version=JOINT_VERSION):
    rows = db.query(ForecastHistory, ForecastGrade).join(ForecastGrade,
        ForecastGrade.forecast_id == ForecastHistory.id).filter(ForecastHistory.market == market,
        ForecastHistory.model_version == version, ForecastHistory.created_at < cutoff,
        ForecastGrade.observed_at < cutoff).order_by(ForecastHistory.created_at, ForecastHistory.id).all()
    return [dict(game_id=f.game_id, created_at=utc(f.created_at), observed_at=utc(g.observed_at),
        scheduled_start=utc(f.scheduled_start), y=g.y,
        **{k: json.loads(f.inputs_json)[k] for k in ("market_p", "model_p")}) for f, g in rows
        if f.scheduled_start is not None and not g.push]


def monthly_fit(db, market, now):
    period = now.strftime("%Y-%m")
    existing = db.query(ShadowFit).filter_by(model_version=JOINT_VERSION, market=market, period=period).first()
    if existing:
        if utc(existing.created_at) > now:
            return None  # Do not replay with a future fit.
        return existing
    cutoff = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)-timedelta(days=1)
    fit = fit_joint(training_rows(db, market, cutoff), cutoff)
    row = ShadowFit(model_version=JOINT_VERSION, market=market, period=period,
                    created_at=now, train_cutoff=cutoff, payload_json=dumps(fit))
    db.add(row)
    db.flush()
    return row


def produce_shadow_forecasts(db, game_id, *, as_of=None):
    now = utc(as_of) or datetime.now(timezone.utc)
    game = db.get(Game, game_id)
    start = utc(game.start_time) if game else None
    if not start or now >= start:
        return []
    # Only a live forecast; shadow rows never recursively become model inputs.
    base = db.query(ForecastHistory).filter(ForecastHistory.game_id == game_id,
        ForecastHistory.market.is_(None), ForecastHistory.created_at <= now).order_by(
        ForecastHistory.created_at.desc(), ForecastHistory.id.desc()).first()
    if not base or utc(base.scheduled_start) != start:
        return []
    outputs = json.loads(base.outputs_json)
    if any(not isinstance(outputs.get(key), (int, float)) or not math.isfinite(outputs[key])
           for key in ("home_win_pct", "projected_total")):
        return []
    rows = []
    for market in ("moneyline", "totals"):
        price = db.query(PriceHistory).filter(PriceHistory.game_id == game_id, PriceHistory.market == market,
            PriceHistory.fetched_at <= now).order_by(PriceHistory.fetched_at.desc(),
            PriceHistory.sportsbook, PriceHistory.id.desc()).first()
        if not price or utc(price.scheduled_start) != start or now-utc(price.fetched_at) > timedelta(minutes=10):
            continue
        model_p = (outputs.get("calibrated_home_win_pct") if outputs.get("calibrated_home_win_pct") is not None
                   else outputs["home_win_pct"]) if market == "moneyline" else float(
                       norm.sf(price.line, loc=outputs["projected_total"], scale=2.5))
        market_p = no_vig(price.first_price, price.second_price)
        if not isinstance(model_p, (int, float)) or not math.isfinite(model_p) or not 0 <= model_p <= 1:
            continue
        fit = monthly_fit(db, market, now)
        if fit is None:
            continue
        probability = joint_probability(json.loads(fit.payload_json), market_p, model_p)
        positive = probability >= market_p
        play = ("home_ml" if positive else "away_ml") if market == "moneyline" else ("over" if positive else "under")
        row = append_forecast(db, game_id=game_id, model_version=JOINT_VERSION,
            calibration_version=f"fit:{fit.id}", created_at=now, market=market,
            sportsbook=price.sportsbook, line=price.line, price_id=price.id,
            inputs={"forecast_id": base.id, "feature_hash": base.feature_hash,
                    "model_p": model_p, "market_p": market_p, "fit_id": fit.id,
                    "projected_total": outputs["projected_total"]},
            outputs={"probability": probability, "model_probability": model_p,
                     "market_probability": market_p, "edge": probability-market_p,
                     "play": play, "shadow_only": True})
        rows.append(row)
        if market == "totals":
            rows.extend(produce_totals_candidates(db, base, price, now, outputs["projected_total"], market_p))
    db.flush()
    from app.services.prospective_gate_service import enroll_forecast
    for row in rows:
        enroll_forecast(db, row)
    return rows


def produce_totals_candidates(db, base, price, now, projected_total, market_p):
    from app.services.totals_shadow_math import (TOTALS_VERSION, TOTALS_NO_FACTOR_VERSION, fit_totals, totals_probability)
    rows = []
    cutoff = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)-timedelta(days=1)
    for version, factor in ((TOTALS_VERSION, True), (TOTALS_NO_FACTOR_VERSION, False)):
        fit = db.query(ShadowFit).filter_by(model_version=version, market="totals", period=now.strftime("%Y-%m")).first()
        if fit is None:
            historical = db.query(ForecastHistory, ForecastGrade).join(ForecastGrade,
                ForecastGrade.forecast_id == ForecastHistory.id).filter(ForecastHistory.model_version == JOINT_VERSION,
                ForecastHistory.market == "totals", ForecastHistory.created_at < cutoff, ForecastGrade.observed_at < cutoff).all()
            training = [dict(game_id=f.game_id, created_at=utc(f.created_at), observed_at=utc(g.observed_at),
                scheduled_start=utc(f.scheduled_start), projected_total=json.loads(f.inputs_json)["projected_total"],
                total=g.home_score+g.away_score) for f, g in historical if f.scheduled_start]
            params = fit_totals(training, cutoff, seasonal_factor=factor)
            if params is None:
                continue
            fit = ShadowFit(model_version=version, market="totals", period=now.strftime("%Y-%m"),
                created_at=now, train_cutoff=cutoff, payload_json=dumps(params))
            db.add(fit)
            db.flush()
        if utc(fit.created_at) > now:
            continue
        result = totals_probability(json.loads(fit.payload_json), projected_total, float(price.line), utc(base.scheduled_start))
        result.update(market_probability=market_p, model_probability=result["probability"],
            edge=result["probability"]-market_p, play="over" if result["probability"] >= market_p else "under", shadow_only=True)
        rows.append(append_forecast(db, game_id=base.game_id, model_version=version, calibration_version=f"fit:{fit.id}",
            created_at=now, market="totals", sportsbook=price.sportsbook, line=price.line, price_id=price.id,
            inputs={"forecast_id":base.id, "fit_id":fit.id, "model_p":result["probability"],
                    "market_p":market_p, "projected_total":projected_total}, outputs=result))
    return rows


def grade_shadow_forecasts(db, *, as_of=None):
    now = utc(as_of) or datetime.now(timezone.utc)
    records = db.query(ForecastHistory, Game).join(Game, Game.game_id == ForecastHistory.game_id).outerjoin(
        ForecastGrade, ForecastGrade.forecast_id == ForecastHistory.id).filter(
        ForecastGrade.id.is_(None), ForecastHistory.market.isnot(None),
        ForecastHistory.created_at < now, ForecastHistory.scheduled_start < now,
        Game.status.in_(["Final", "Game Over", "Completed Early"])).all()
    count = 0
    for forecast, game in records:
        home, away = game.final_home_score, game.final_away_score
        if home is None or away is None or min(home, away) < 0 or home+away == 0:
            continue
        reviews = db.query(GameOutcomeReview).filter(GameOutcomeReview.game_id == game.game_id).all()
        if any(r.final_home_score != home or r.final_away_score != away for r in reviews):
            continue
        if utc(forecast.created_at) >= utc(forecast.scheduled_start):
            continue
        push = home == away if forecast.market == "moneyline" else home+away == forecast.line
        y = int(home > away) if forecast.market == "moneyline" else int(home+away > forecast.line)
        score = proper_scores([y], [json.loads(forecast.outputs_json)["probability"]]) if not push else {}
        db.add(ForecastGrade(forecast_id=forecast.id, observed_at=now, home_score=home, away_score=away,
            y=None if push else y, push=push, brier=score.get("brier"), log_loss=score.get("log_loss")))
        count += 1
    db.commit()
    return {"graded": count}


def shadow_score_report(db):
    groups = {}
    for f, g in db.query(ForecastHistory, ForecastGrade).join(ForecastGrade,
            ForecastGrade.forecast_id == ForecastHistory.id).order_by(ForecastHistory.created_at, ForecastHistory.id).all():
        key = (f.model_version, f.market)
        groups.setdefault(key, {}).setdefault(f.game_id, g)
    return [dict(model_version=v, market=m, games=len(rows), graded=sum(not g.push for g in rows.values()),
        pushes=sum(g.push for g in rows.values()),
        brier=sum(g.brier for g in rows.values() if not g.push)/sum(not g.push for g in rows.values()) if any(not g.push for g in rows.values()) else None,
        log_loss=sum(g.log_loss for g in rows.values() if not g.push)/sum(not g.push for g in rows.values()) if any(not g.push for g in rows.values()) else None)
        for (v, m), rows in groups.items()]
