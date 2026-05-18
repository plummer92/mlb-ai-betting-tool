from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.config import DEBUG
from app.middleware.auth import verify_api_key
from app.middleware.limiter import limiter
from app.models.schema import EdgeResult, Game, GameOdds, Prediction
from app.scheduler import scheduler
from app.services.betting_policy import get_betting_profile, qualifies_for_bet_policy
from app.services.market_respect_service import market_respect_adjustment, market_respect_for_edge
from app.services.odds_service import is_odds_snapshot_fresh

router = APIRouter(prefix="/api/debug", tags=["debug"])
logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
MIN_DIAGNOSTIC_KELLY_FRACTION = 0.001


@router.get("/jobs", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def list_scheduler_jobs(request: Request):
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
        })
    return {"job_count": len(jobs), "jobs": jobs}


@router.get("/flags")
def debug_flags():
    return {"debug": DEBUG}


def _stage_payload(filtered: list[dict]) -> dict:
    reasons = Counter(reason for row in filtered for reason in row["reasons"])
    return {
        "filtered_count": len(filtered),
        "top_rejection_reasons": [
            {"reason": reason, "count": count}
            for reason, count in reasons.most_common(5)
        ],
        "sample_rejected_plays": filtered[:5],
    }


def _policy_rejection_reasons(
    *,
    play: str | None,
    edge_pct: float | None,
    ev: float | None,
    confidence: str | None,
    confidence_score: float | None = None,
) -> list[str]:
    profile = get_betting_profile(play)
    if not profile.get("enabled"):
        return ["disabled_market"]

    reasons: list[str] = []
    edge = float(edge_pct or 0.0)
    expected_value = float(ev or 0.0)
    normalized_confidence = (confidence or "").strip().lower()

    if expected_value < float(profile["min_ev"]):
        reasons.append("ev_below_threshold")
    if edge < float(profile["min_edge"]):
        reasons.append("edge_below_threshold")

    max_edge = profile.get("max_edge")
    if max_edge is not None and edge >= float(max_edge):
        reasons.append("edge_above_policy_band")

    allowed_confidences = profile.get("allowed_confidences") or set()
    if allowed_confidences and normalized_confidence not in allowed_confidences:
        reasons.append("confidence_too_low")

    min_cs = profile.get("min_confidence_score")
    if min_cs is not None and confidence_score is not None and confidence_score < float(min_cs):
        reasons.append("confidence_score_too_low")

    return reasons


def _edge_ev(edge: EdgeResult) -> float:
    play = (edge.recommended_play or "").lower()
    if play == "away_ml":
        return float(edge.ev_away or 0)
    if play == "home_ml":
        return float(edge.ev_home or 0)
    if play == "over":
        return float(edge.ev_over or 0)
    if play == "under":
        return float(edge.ev_under or 0)
    return 0.0


def _play_odds(edge: EdgeResult, odds: GameOdds | None) -> int | None:
    play = (edge.recommended_play or "").lower()
    if play == "away_ml":
        return edge.away_ml if edge.away_ml is not None else (odds.away_ml if odds else None)
    if play == "home_ml":
        return edge.home_ml if edge.home_ml is not None else (odds.home_ml if odds else None)
    if play == "over":
        return edge.over_odds if edge.over_odds is not None else (odds.over_odds if odds else None)
    if play == "under":
        return edge.under_odds if edge.under_odds is not None else (odds.under_odds if odds else None)
    return None


def _reject_sample(game: Game, reasons: list[str], edge: EdgeResult | None = None, adjustment: dict | None = None) -> dict:
    return {
        "game_id": game.game_id,
        "matchup": f"{game.away_team} @ {game.home_team}",
        "play": edge.recommended_play if edge else None,
        "raw_edge_pct": float(edge.edge_pct or 0) if edge else None,
        "adjusted_edge_pct": adjustment.get("adjusted_edge_pct") if adjustment else None,
        "market_trust_score": adjustment.get("score") if adjustment else None,
        "adjusted_kelly_fraction": adjustment.get("adjusted_kelly_fraction") if adjustment else None,
        "reasons": reasons,
    }


def build_decision_pipeline_diagnostics(db: Session) -> dict:
    today = datetime.now(ET).date()
    games = db.query(Game).filter(Game.game_date == today).all()
    game_ids = [game.game_id for game in games]
    odds_by_game: dict[int, GameOdds] = {}
    projection_by_game: dict[int, Prediction] = {}
    edge_by_game: dict[int, EdgeResult] = {}

    if game_ids:
        odds_rows = (
            db.query(GameOdds)
            .filter(GameOdds.game_id.in_(game_ids))
            .order_by(GameOdds.game_id.asc(), GameOdds.fetched_at.desc(), GameOdds.id.desc())
            .all()
        )
        for odds in odds_rows:
            odds_by_game.setdefault(odds.game_id, odds)

        projection_rows = (
            db.query(Prediction)
            .filter(Prediction.game_id.in_(game_ids), Prediction.is_active.is_(True))
            .order_by(Prediction.game_id.asc(), Prediction.prediction_id.desc())
            .all()
        )
        for projection in projection_rows:
            projection_by_game.setdefault(projection.game_id, projection)

        edge_rows = (
            db.query(EdgeResult)
            .filter(EdgeResult.game_id.in_(game_ids), EdgeResult.is_active.is_(True))
            .order_by(EdgeResult.game_id.asc(), EdgeResult.calculated_at.desc(), EdgeResult.id.desc())
            .all()
        )
        for edge in edge_rows:
            edge_by_game.setdefault(edge.game_id, edge)

    missing_odds = []
    missing_projection = []
    raw_rejected = []
    raw_positive = []
    for game in games:
        odds = odds_by_game.get(game.game_id)
        projection = projection_by_game.get(game.game_id)
        edge = edge_by_game.get(game.game_id)
        if odds is None:
            missing_odds.append(_reject_sample(game, ["missing_odds"], edge))
        if projection is None:
            missing_projection.append(_reject_sample(game, ["missing_projection"], edge))
        if not edge or not edge.recommended_play or float(edge.edge_pct or 0) <= 0:
            raw_rejected.append(_reject_sample(game, ["no_positive_raw_edge"], edge))
            continue
        raw_positive.append((game, edge, odds, projection))

    market_rejected = []
    market_survivors = []
    for game, edge, odds, projection in raw_positive:
        respect = market_respect_for_edge(db, edge, odds=odds, game=game)
        adjustment = market_respect_adjustment(
            edge_pct=float(edge.edge_pct or 0),
            ev=_edge_ev(edge),
            confidence=edge.confidence_tier,
            market_respect=respect,
            odds_american=_play_odds(edge, odds),
        )
        reasons = []
        if "MARKET REJECTED" in adjustment["tags"] or adjustment["bucket"] == "market_rejection":
            reasons.append("market_rejection")
        if adjustment["adjusted_edge_pct"] <= 0:
            reasons.append("negative_adjusted_edge")
        row = (game, edge, odds, projection, respect, adjustment)
        if reasons:
            market_rejected.append(_reject_sample(game, reasons, edge, adjustment))
        else:
            market_survivors.append(row)

    stale_rejected = []
    stale_survivors = []
    for game, edge, odds, projection, respect, adjustment in market_survivors:
        reasons = []
        if odds is None:
            reasons.append("missing_odds")
        elif not is_odds_snapshot_fresh(odds):
            reasons.append("stale_odds")
        if "STALE OPEN" in adjustment["tags"]:
            reasons.append("stale_open")
        if reasons:
            stale_rejected.append(_reject_sample(game, reasons, edge, adjustment))
        else:
            stale_survivors.append((game, edge, odds, projection, respect, adjustment))

    confidence_rejected = []
    confidence_survivors = []
    for game, edge, odds, projection, respect, adjustment in stale_survivors:
        policy_reasons = _policy_rejection_reasons(
            play=edge.recommended_play,
            edge_pct=adjustment["adjusted_edge_pct"],
            ev=adjustment["adjusted_ev"],
            confidence=adjustment["adjusted_confidence"],
            confidence_score=projection.confidence_score if projection else None,
        )
        if policy_reasons or not qualifies_for_bet_policy(
            play=edge.recommended_play,
            edge_pct=adjustment["adjusted_edge_pct"],
            ev=adjustment["adjusted_ev"],
            confidence=adjustment["adjusted_confidence"],
            confidence_score=projection.confidence_score if projection else None,
        ):
            confidence_rejected.append(_reject_sample(game, policy_reasons or ["confidence_too_low"], edge, adjustment))
        else:
            confidence_survivors.append((game, edge, odds, projection, respect, adjustment))

    kelly_rejected = []
    final_rows = []
    for game, edge, odds, projection, respect, adjustment in confidence_survivors:
        if adjustment["adjusted_kelly_fraction"] < MIN_DIAGNOSTIC_KELLY_FRACTION:
            kelly_rejected.append(_reject_sample(game, ["kelly_below_threshold"], edge, adjustment))
        else:
            final_rows.append((game, edge, odds, projection, respect, adjustment))

    fire_ready_rows = [
        row
        for row in final_rows
        if int(row[4].get("score", 50)) >= 70
        and row[5].get("alert_allowed")
        and float(row[5].get("adjusted_edge_pct") or 0) > 0
        and float(row[5].get("adjusted_ev") or 0) > 0
    ]

    counts = {
        "total_games": len(games),
        "games_with_odds": len({game_id for game_id in game_ids if game_id in odds_by_game}),
        "games_with_model_projection": len({game_id for game_id in game_ids if game_id in projection_by_game}),
        "raw_positive_edges": len(raw_positive),
        "after_market_respect_filter": len(market_survivors),
        "after_stale_odds_filter": len(stale_survivors),
        "after_confidence_filter": len(confidence_survivors),
        "after_kelly_filter": len(final_rows),
        "final_ranked_plays": len(final_rows),
        "fire_ready_plays": len(fire_ready_rows),
    }
    stages = {
        "games_with_odds": _stage_payload(missing_odds),
        "games_with_model_projection": _stage_payload(missing_projection),
        "raw_positive_edges": _stage_payload(raw_rejected),
        "after_market_respect_filter": _stage_payload(market_rejected),
        "after_stale_odds_filter": _stage_payload(stale_rejected),
        "after_confidence_filter": _stage_payload(confidence_rejected),
        "after_kelly_filter": _stage_payload(kelly_rejected),
        "final_ranked_plays": _stage_payload([]),
    }
    report = {"date": today.isoformat(), "counts": counts, "stages": stages}
    if counts["final_ranked_plays"] == 0:
        logger.warning(
            "No plays survived decision pipeline",
            extra={
                "event": "decision_pipeline_zero_survivors",
                "decision_pipeline": counts,
            },
        )
    return report


@router.get("/decision-pipeline")
def decision_pipeline(db: Session = Depends(get_db)):
    return build_decision_pipeline_diagnostics(db)
