from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from scipy.stats import norm
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.config import DEBUG
from app.middleware.auth import verify_api_key
from app.middleware.limiter import limiter
from app.models.schema import EdgeResult, Game, GameOdds, Prediction, SnapshotType
from app.scheduler import scheduler
from app.services.betting_policy import get_betting_profile, qualifies_for_bet_policy
from app.services.edge_service import (
    TOTAL_STD_DEV,
    calculate_all_edges_today,
    get_edge_persistence_failures,
    validate_active_edge_lineage,
)
from app.services.ev_math import calc_edge, implied_prob_raw, remove_vig
from app.services.market_respect_service import market_respect_adjustment, market_respect_for_edge
from app.services.odds_service import odds_freshness_metadata

router = APIRouter(prefix="/api/debug", tags=["debug"])
logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
MIN_DIAGNOSTIC_KELLY_FRACTION = 0.001
RAW_EDGE_THRESHOLD = 0.0
DEBUG_NEAR_EDGE_THRESHOLD = -0.005


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


@router.get("/routes")
def debug_routes(request: Request):
    routes = []
    for route in request.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/debug"):
            continue
        routes.append({
            "path": path,
            "name": getattr(route, "name", None),
            "methods": sorted(getattr(route, "methods", []) or []),
        })
    return {"count": len(routes), "routes": sorted(routes, key=lambda row: row["path"])}


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


def _reject_sample(
    game: Game,
    reasons: list[str],
    edge: EdgeResult | None = None,
    adjustment: dict | None = None,
    freshness: dict | None = None,
    raw: dict | None = None,
) -> dict:
    freshness = freshness or {}
    raw = raw or {}
    return {
        "game_id": game.game_id,
        "matchup": f"{game.away_team} @ {game.home_team}",
        "play": edge.recommended_play if edge else None,
        "raw_edge_pct": float(edge.edge_pct or 0) if edge else None,
        "adjusted_edge_pct": adjustment.get("adjusted_edge_pct") if adjustment else None,
        "market_trust_score": adjustment.get("score") if adjustment else None,
        "adjusted_kelly_fraction": adjustment.get("adjusted_kelly_fraction") if adjustment else None,
        "odds_last_updated": freshness.get("odds_last_updated"),
        "minutes_since_update": freshness.get("minutes_since_update"),
        "market_last_movement": freshness.get("market_last_movement"),
        "opening_line_timestamp": freshness.get("opening_line_timestamp"),
        "closing_line_timestamp": freshness.get("closing_line_timestamp"),
        "freshness_threshold_minutes": freshness.get("freshness_threshold_minutes"),
        "freshness_status": freshness.get("status"),
        "best_raw_edge_pct": raw.get("best_raw_edge_pct"),
        "best_raw_play": raw.get("best_raw_play"),
        "raw_edge_threshold": raw.get("raw_edge_threshold"),
        "raw_edge_status": raw.get("raw_edge_status"),
        "reasons": reasons,
    }


def build_decision_pipeline_diagnostics(db: Session) -> dict:
    today = datetime.now(ET).date()
    games = db.query(Game).filter(Game.game_date == today).all()
    game_ids = [game.game_id for game in games]
    odds_by_game: dict[int, GameOdds] = {}
    projection_by_game: dict[int, Prediction] = {}
    edge_by_game: dict[int, EdgeResult] = {}
    raw_board = build_raw_edge_board(db)
    raw_by_game = {row["game_id"]: row for row in raw_board["games"]}

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
    raw_accepted = []
    raw_positive = []
    for game in games:
        odds = odds_by_game.get(game.game_id)
        projection = projection_by_game.get(game.game_id)
        edge = edge_by_game.get(game.game_id)
        raw = raw_by_game.get(game.game_id, {})
        raw_status = _raw_edge_acceptance(raw)
        if odds is None:
            missing_odds.append(_reject_sample(game, ["missing_odds"], edge, raw=raw))
        if projection is None:
            missing_projection.append(_reject_sample(game, ["missing_projection"], edge, raw=raw))
        if raw_status["accepted"]:
            raw_accepted.append((game, raw_status))
        if not edge or not edge.recommended_play or float(edge.edge_pct or 0) <= 0:
            reasons = []
            if not raw_status["accepted"]:
                reasons.append(raw_status["reason"])
            elif not edge:
                reasons.append("missing_persisted_edge_result")
            elif not edge.recommended_play:
                reasons.append("missing_persisted_recommended_play")
            else:
                reasons.append("persisted_edge_non_positive")
            raw_rejected.append(_reject_sample(game, reasons, edge, raw=raw_status))
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
        freshness = odds_freshness_metadata(db, game=game, odds_row=odds)
        if odds is None:
            reasons.append("missing_odds")
        elif freshness["status"] == "stale_feed":
            reasons.append("stale_feed")
        elif freshness["status"] == "stale_open":
            reasons.append("stale_open")
        if "STALE OPEN" in adjustment["tags"]:
            reasons.append(freshness["status"] if freshness["status"] in {"stale_feed", "stale_open"} else "stale_open")
        reasons = list(dict.fromkeys(reasons))
        if reasons:
            stale_rejected.append(_reject_sample(game, reasons, edge, adjustment, freshness))
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
        "raw_positive_edges": len(raw_accepted),
        "persisted_raw_positive_edges": len(raw_positive),
        "debug_near_edges": len([_game for _game, status in raw_accepted if status["status"] == "DEBUG_NEAR_EDGE"]),
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
    report = {
        "date": today.isoformat(),
        "counts": counts,
        "stages": stages,
        "raw_edge_summary": raw_board["summary"],
    }
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


def build_odds_freshness_report(db: Session) -> dict:
    today = datetime.now(ET).date()
    games = db.query(Game).filter(Game.game_date == today).all()
    game_ids = [game.game_id for game in games]
    now_utc = datetime.now(timezone.utc)
    latest_by_game: dict[int, GameOdds] = {}

    if game_ids:
        rows = (
            db.query(GameOdds)
            .filter(GameOdds.game_id.in_(game_ids))
            .order_by(GameOdds.game_id.asc(), GameOdds.fetched_at.desc(), GameOdds.id.desc())
            .all()
        )
        for odds in rows:
            latest_by_game.setdefault(odds.game_id, odds)

    rows_by_book = (
        db.query(GameOdds)
        .filter(GameOdds.game_id.in_(game_ids))
        .all()
        if game_ids
        else []
    )
    per_book: dict[str, dict] = {}
    for odds in rows_by_book:
        book = odds.sportsbook or "unknown"
        bucket = per_book.setdefault(book, {"rows": 0, "fresh": 0, "quiet_market": 0, "stale": 0, "latest_sync": None})
        game = next((item for item in games if item.game_id == odds.game_id), None)
        freshness = odds_freshness_metadata(db, game=game, odds_row=odds, now=now_utc)
        bucket["rows"] += 1
        if freshness["status"] == "fresh":
            bucket["fresh"] += 1
        elif freshness["status"] == "quiet_market":
            bucket["quiet_market"] += 1
        elif freshness["status"] in {"stale_feed", "stale_open"}:
            bucket["stale"] += 1
        if odds.fetched_at:
            fetched = odds.fetched_at.isoformat()
            bucket["latest_sync"] = max(bucket["latest_sync"] or fetched, fetched)

    freshness_rows = []
    reason_counts: Counter[str] = Counter()
    ages = []
    oldest = None
    newest = None
    for game in games:
        odds = latest_by_game.get(game.game_id)
        freshness = odds_freshness_metadata(db, game=game, odds_row=odds, now=now_utc)
        if freshness["status"] not in {"fresh", "quiet_market"}:
            reason_counts[freshness["reason"]] += 1
        if freshness["minutes_since_update"] is not None:
            ages.append(float(freshness["minutes_since_update"]))
        if freshness["odds_last_updated"]:
            oldest = min(oldest or freshness["odds_last_updated"], freshness["odds_last_updated"])
            newest = max(newest or freshness["odds_last_updated"], freshness["odds_last_updated"])
        freshness_rows.append({
            "game_id": game.game_id,
            "matchup": f"{game.away_team} @ {game.home_team}",
            "sportsbook": odds.sportsbook if odds else None,
            **freshness,
        })

    fresh_games = sum(1 for row in freshness_rows if row["status"] == "fresh")
    quiet_games = sum(1 for row in freshness_rows if row["status"] == "quiet_market")
    stale_games = sum(1 for row in freshness_rows if row["status"] in {"stale_feed", "stale_open"})
    report = {
        "date": today.isoformat(),
        "total_games": len(games),
        "fresh_games": fresh_games,
        "quiet_market_games": quiet_games,
        "stale_games": stale_games,
        "avg_minutes_since_update": round(sum(ages) / len(ages), 1) if ages else None,
        "oldest_line": oldest,
        "newest_line": newest,
        "per_book_freshness": per_book,
        "stale_reason_counts": [{"reason": reason, "count": count} for reason, count in reason_counts.most_common()],
        "games": freshness_rows,
    }
    logger.warning(
        "[odds freshness] games=%s fresh=%s quiet=%s stale=%s",
        report["total_games"],
        report["fresh_games"],
        report["quiet_market_games"],
        report["stale_games"],
        extra={"event": "odds_freshness", "odds_freshness": report},
    )
    return report


@router.get("/odds-freshness")
def odds_freshness(db: Session = Depends(get_db)):
    return build_odds_freshness_report(db)


def _edge_result_sample(edge: EdgeResult, game: Game | None = None) -> dict:
    return {
        "edge_id": edge.id,
        "game_id": edge.game_id,
        "matchup": f"{game.away_team} @ {game.home_team}" if game else None,
        "prediction_id": edge.prediction_id,
        "odds_id": edge.odds_id,
        "run_stage": edge.run_stage,
        "is_active": edge.is_active,
        "play_type": edge.recommended_play,
        "edge_pct": float(edge.edge_pct or 0),
        "confidence_tier": edge.confidence_tier,
        "sportsbook": edge.sportsbook,
        "odds_snapshot_type": edge.odds_snapshot_type,
        "calculated_at": edge.calculated_at.isoformat() if edge.calculated_at else None,
    }


def _edge_schema_diagnostics() -> dict:
    table = EdgeResult.__table__
    return {
        "required_columns": [
            column.name
            for column in table.columns
            if not column.nullable and not column.primary_key
        ],
        "nullable_columns": [
            column.name
            for column in table.columns
            if column.nullable and not column.primary_key
        ],
        "unique_constraints": [
            {
                "name": constraint.name,
                "columns": [column.name for column in constraint.columns],
            }
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        ],
        "retrieval_filters": [
            "EdgeResult.is_active == true",
            "Prediction.is_active == true",
            "Game.game_date == today",
            "validate_active_edge_lineage(edge, prediction, odds)",
        ],
    }


def _edge_retrieval_diagnostics(db: Session, *, limit: int = 50) -> dict:
    today = datetime.now(ET).date()
    rows = (
        db.query(EdgeResult, Game, Prediction, GameOdds)
        .outerjoin(Game, Game.game_id == EdgeResult.game_id)
        .outerjoin(Prediction, Prediction.prediction_id == EdgeResult.prediction_id)
        .outerjoin(GameOdds, GameOdds.id == EdgeResult.odds_id)
        .order_by(EdgeResult.calculated_at.desc(), EdgeResult.id.desc())
        .limit(limit)
        .all()
    )
    diagnostics = []
    reason_counts: Counter[str] = Counter()
    for edge, game, prediction, odds in rows:
        reasons: list[str] = []
        if game is None:
            reasons.append("missing_game_linkage")
        elif game.game_date != today:
            reasons.append("not_today")
        if prediction is None:
            reasons.append("missing_prediction_linkage")
        elif not prediction.is_active:
            reasons.append("inactive_prediction")
        if not edge.is_active:
            reasons.append("inactive_edge")
        if odds is None:
            reasons.append("missing_odds_linkage")
        if prediction is not None and odds is not None and game is not None:
            is_valid, lineage_reason = validate_active_edge_lineage(edge, prediction, odds, db=db, game=game)
            if not is_valid:
                if lineage_reason == "stale_odds_snapshot":
                    reasons.append("stale_odds_linkage")
                reasons.append(lineage_reason or "failed_lineage_validation")
        if not reasons:
            reasons.append("ranked_query_eligible")
        for reason in set(reasons):
            reason_counts[reason] += 1
        diagnostics.append({
            **_edge_result_sample(edge, game),
            "prediction_is_active": prediction.is_active if prediction else None,
            "prediction_run_stage": prediction.run_stage if prediction else None,
            "odds_snapshot_id": odds.id if odds else None,
            "odds_snapshot_type": odds.snapshot_type.value if odds and odds.snapshot_type else None,
            "odds_fetched_at": odds.fetched_at.isoformat() if odds and odds.fetched_at else None,
            "retrieval_reasons": reasons,
        })
    return {
        "date": today.isoformat(),
        "rows_checked": len(diagnostics),
        "reason_counts": [{"reason": reason, "count": count} for reason, count in reason_counts.most_common()],
        "edges": diagnostics,
    }


def build_edge_db_state(db: Session) -> dict:
    latest_rows = (
        db.query(EdgeResult, Game)
        .outerjoin(Game, Game.game_id == EdgeResult.game_id)
        .order_by(EdgeResult.calculated_at.desc(), EdgeResult.id.desc())
        .limit(20)
        .all()
    )
    return {
        "latest_edge_results": [
            _edge_result_sample(edge, game)
            for edge, game in latest_rows
        ],
        "retrieval_diagnostics": _edge_retrieval_diagnostics(db),
    }


@router.get("/edge-db-state")
def edge_db_state(db: Session = Depends(get_db)):
    return build_edge_db_state(db)


def build_edge_persistence_report(db: Session) -> dict:
    today = datetime.now(ET).date()
    raw_board = build_raw_edge_board(db)
    computed_rows = [row for row in raw_board["games"] if row.get("raw_edge_accepted")]
    computed_by_game = {row["game_id"]: row for row in computed_rows}

    persisted_rows = (
        db.query(EdgeResult, Game)
        .join(Game, Game.game_id == EdgeResult.game_id)
        .filter(Game.game_date == today)
        .order_by(EdgeResult.calculated_at.desc(), EdgeResult.id.desc())
        .all()
    )
    persisted_by_game: dict[int, EdgeResult] = {}
    for edge, _game in persisted_rows:
        persisted_by_game.setdefault(edge.game_id, edge)

    active_positive_rows = [
        (edge, game)
        for edge, game in persisted_rows
        if edge.is_active and edge.recommended_play and edge.edge_pct is not None and float(edge.edge_pct) > 0
    ]

    latest_persisted = (
        db.query(func.max(EdgeResult.calculated_at))
        .join(Game, Game.game_id == EdgeResult.game_id)
        .filter(Game.game_date == today)
        .scalar()
    )
    failures = get_edge_persistence_failures()
    latest_failure_by_game = {
        int(row["game_id"]): row
        for row in failures
        if row.get("game_id") is not None
    }

    sample_missing = []
    for row in computed_rows:
        if row["game_id"] in persisted_by_game:
            continue
        failure = latest_failure_by_game.get(row["game_id"])
        missing = {
            "game_id": row["game_id"],
            "matchup": row.get("matchup"),
            "play_type": row.get("best_raw_play"),
            "edge_pct": row.get("best_raw_edge_pct"),
            "reason": "missing_persisted_edge_result",
            "db_exception": failure.get("db_exception") if failure else None,
            "failure_timestamp": failure.get("timestamp") if failure else None,
        }
        logger.warning(
            "[edge persist] missing game_id=%s play_type=%s edge_pct=%s error=%s",
            missing["game_id"],
            missing["play_type"],
            missing["edge_pct"],
            missing["db_exception"],
            extra={"event": "edge_persist_missing", "edge_persist_missing": missing},
        )
        sample_missing.append(missing)

    computed_count = len(computed_rows)
    persisted_count = len({edge.game_id for edge, _game in persisted_rows if edge.game_id in computed_by_game})
    active_positive_count = len({edge.game_id for edge, _game in active_positive_rows if edge.game_id in computed_by_game})
    missing_persist_count = max(computed_count - persisted_count, 0)
    retrieval = _edge_retrieval_diagnostics(db)
    report = {
        "date": today.isoformat(),
        "computed_edge_count": computed_count,
        "persisted_edge_count": persisted_count,
        "active_positive_edge_count": active_positive_count,
        "failed_persist_count": missing_persist_count,
        "latest_persisted_edge_timestamp": latest_persisted.isoformat() if latest_persisted else None,
        "latest_computed_edge_timestamp": datetime.now(timezone.utc).isoformat() if computed_rows else None,
        "recorded_persistence_failures": failures[-10:],
        "sample_persisted_edges": [
            _edge_result_sample(edge, game)
            for edge, game in persisted_rows[:5]
        ],
        "sample_missing_edges": sample_missing[:5],
        "retrieval_diagnostics": retrieval,
        "schema_diagnostics": _edge_schema_diagnostics(),
    }
    logger.warning(
        "[edge persist] computed=%s persisted=%s failed=%s",
        computed_count,
        persisted_count,
        missing_persist_count,
        extra={"event": "edge_persist_diagnostic", "edge_persist": report},
    )
    return report


@router.get("/edge-persistence")
def edge_persistence(db: Session = Depends(get_db)):
    return build_edge_persistence_report(db)


@router.post("/rebuild-edge-results")
def rebuild_edge_results(
    run_stage: str = Query("daily_open"),
    snapshot_type: str = Query("open"),
    db: Session = Depends(get_db),
):
    try:
        snapshot = SnapshotType(snapshot_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unsupported snapshot_type: {snapshot_type}") from exc
    results = calculate_all_edges_today(
        db,
        run_stage=run_stage,
        snapshot_type=snapshot,
        fallback_policy="reuse_fresh_same_stage",
    )
    persisted = sum(1 for row in results if row.get("status") == "created")
    failed = sum(1 for row in results if row.get("reason") == "edge_persist_failed")
    skipped = sum(1 for row in results if row.get("status") == "skipped")
    return {
        "run_stage": run_stage,
        "snapshot_type": snapshot.value,
        "computed_attempts": persisted + failed,
        "persisted": persisted,
        "failed": failed,
        "skipped": skipped,
        "results": [
            {key: value for key, value in row.items() if key != "edge"}
            for row in results
        ],
        "edge_persistence": build_edge_persistence_report(db),
    }


def _moneyline_probabilities(prediction: Prediction | None, odds: GameOdds | None) -> dict:
    if prediction is None:
        return {
            "model_prob_home": None,
            "model_prob_away": None,
            "sportsbook_home_implied_prob": None,
            "sportsbook_away_implied_prob": None,
            "home_edge_pct": None,
            "away_edge_pct": None,
        }
    model_home = float(prediction.calibrated_home_win_pct or prediction.home_win_pct)
    model_away = float(prediction.calibrated_away_win_pct or prediction.away_win_pct)
    if odds is None or odds.away_ml is None or odds.home_ml is None:
        return {
            "model_prob_home": round(model_home, 4),
            "model_prob_away": round(model_away, 4),
            "sportsbook_home_implied_prob": None,
            "sportsbook_away_implied_prob": None,
            "home_edge_pct": None,
            "away_edge_pct": None,
        }
    raw_away = implied_prob_raw(int(odds.away_ml))
    raw_home = implied_prob_raw(int(odds.home_ml))
    implied_away, implied_home = remove_vig(raw_away, raw_home)
    return {
        "model_prob_home": round(model_home, 4),
        "model_prob_away": round(model_away, 4),
        "sportsbook_home_implied_prob": round(implied_home, 4),
        "sportsbook_away_implied_prob": round(implied_away, 4),
        "home_edge_pct": round(calc_edge(model_home, implied_home), 4),
        "away_edge_pct": round(calc_edge(model_away, implied_away), 4),
    }


def _total_edges(prediction: Prediction | None, odds: GameOdds | None) -> dict:
    projected_total = float(prediction.projected_total) if prediction is not None and prediction.projected_total is not None else None
    current_total = float(odds.total_line) if odds is not None and odds.total_line is not None else None
    if projected_total is None or current_total is None or odds is None or odds.over_odds is None or odds.under_odds is None:
        return {
            "total_edge_pct": None,
            "total_edge_over_pct": None,
            "total_edge_under_pct": None,
            "current_total": current_total,
            "projected_total": projected_total,
        }
    model_over = float(1 - norm.cdf(current_total, loc=projected_total, scale=TOTAL_STD_DEV))
    model_under = 1 - model_over
    raw_over = implied_prob_raw(int(odds.over_odds))
    raw_under = implied_prob_raw(int(odds.under_odds))
    implied_over, implied_under = remove_vig(raw_over, raw_under)
    edge_over = calc_edge(model_over, implied_over)
    edge_under = calc_edge(model_under, implied_under)
    best_total = edge_over if edge_over >= edge_under else edge_under
    return {
        "total_edge_pct": round(best_total, 4),
        "total_edge_over_pct": round(edge_over, 4),
        "total_edge_under_pct": round(edge_under, 4),
        "current_total": current_total,
        "projected_total": projected_total,
    }


def _best_raw_play(row: dict) -> tuple[str | None, float | None]:
    candidates = [
        ("home_ml", row.get("home_edge_pct")),
        ("away_ml", row.get("away_edge_pct")),
        ("over", row.get("total_edge_over_pct")),
        ("under", row.get("total_edge_under_pct")),
    ]
    usable = [(play, float(edge)) for play, edge in candidates if edge is not None]
    if not usable:
        return None, None
    return max(usable, key=lambda item: item[1])


def _raw_edge_acceptance(row: dict) -> dict:
    edge = row.get("best_raw_edge_pct")
    threshold = DEBUG_NEAR_EDGE_THRESHOLD if DEBUG else RAW_EDGE_THRESHOLD
    if edge is None:
        accepted = False
        status = "REJECTED"
        reason = "missing_raw_edge"
    else:
        edge_value = float(edge)
        if edge_value > RAW_EDGE_THRESHOLD:
            accepted = True
            status = "RAW_POSITIVE_EDGE"
            reason = "raw_positive_edge"
        elif DEBUG and edge_value > DEBUG_NEAR_EDGE_THRESHOLD:
            accepted = True
            status = "DEBUG_NEAR_EDGE"
            reason = "debug_near_edge"
        else:
            accepted = False
            status = "REJECTED"
            reason = "no_positive_raw_edge"
    return {
        **row,
        "accepted": accepted,
        "reason": reason,
        "raw_edge_status": status,
        "status": status,
        "raw_edge_threshold": threshold,
    }


def _raw_board_summary(rows: list[dict]) -> dict:
    values = [float(row["best_raw_edge_pct"]) for row in rows if row.get("best_raw_edge_pct") is not None]
    closest = None
    if rows:
        rows_with_edge = [row for row in rows if row.get("best_raw_edge_pct") is not None]
        negative_rows = [row for row in rows_with_edge if float(row["best_raw_edge_pct"]) <= 0]
        if negative_rows:
            closest = max(negative_rows, key=lambda row: float(row["best_raw_edge_pct"]))
        elif rows_with_edge:
            closest = min(rows_with_edge, key=lambda row: float(row["best_raw_edge_pct"]))
    return {
        "max_raw_edge_pct": round(max(values), 4) if values else None,
        "avg_raw_edge_pct": round(sum(values) / len(values), 4) if values else None,
        "closest_to_positive_edge": closest,
        "count_edges_between_-2_and_0": sum(1 for value in values if -0.02 <= value < 0),
        "count_edges_between_0_and_2": sum(1 for value in values if 0 <= value < 0.02),
        "count_edges_above_2": sum(1 for value in values if value >= 0.02),
    }


def build_raw_edge_board(db: Session) -> dict:
    today = datetime.now(ET).date()
    games = db.query(Game).filter(Game.game_date == today).order_by(Game.start_time.asc(), Game.game_id.asc()).all()
    game_ids = [game.game_id for game in games]
    prediction_by_game: dict[int, Prediction] = {}
    odds_by_game: dict[int, GameOdds] = {}

    if game_ids:
        predictions = (
            db.query(Prediction)
            .filter(Prediction.game_id.in_(game_ids), Prediction.is_active.is_(True))
            .order_by(Prediction.game_id.asc(), Prediction.prediction_id.desc())
            .all()
        )
        for prediction in predictions:
            prediction_by_game.setdefault(prediction.game_id, prediction)

        odds_rows = (
            db.query(GameOdds)
            .filter(GameOdds.game_id.in_(game_ids))
            .order_by(GameOdds.game_id.asc(), GameOdds.fetched_at.desc(), GameOdds.id.desc())
            .all()
        )
        for odds in odds_rows:
            odds_by_game.setdefault(odds.game_id, odds)

    rows: list[dict] = []
    for game in games:
        prediction = prediction_by_game.get(game.game_id)
        odds = odds_by_game.get(game.game_id)
        row = {
            "game_id": game.game_id,
            "matchup": f"{game.away_team} @ {game.home_team}",
            "current_moneyline": {
                "away_ml": odds.away_ml if odds else None,
                "home_ml": odds.home_ml if odds else None,
                "sportsbook": odds.sportsbook if odds else None,
            },
        }
        row.update(_moneyline_probabilities(prediction, odds))
        row.update(_total_edges(prediction, odds))
        best_play, best_edge = _best_raw_play(row)
        row["best_raw_play"] = best_play
        row["best_raw_edge_pct"] = round(best_edge, 4) if best_edge is not None else None
        raw_status = _raw_edge_acceptance(row)
        row["raw_edge_threshold"] = raw_status["raw_edge_threshold"]
        row["raw_edge_accepted"] = raw_status["accepted"]
        row["raw_edge_status"] = raw_status["raw_edge_status"]
        row["raw_edge_rejection_reason"] = None if raw_status["accepted"] else raw_status["reason"]
        logger.info(
            "[raw edge] game=%s best_raw_edge_pct=%s threshold=%s accepted=%s reason=%s",
            game.game_id,
            row["best_raw_edge_pct"],
            row["raw_edge_threshold"],
            row["raw_edge_accepted"],
            raw_status["reason"],
            extra={
                "event": "raw_edge_diagnostic",
                "raw_edge": {
                    "game_id": game.game_id,
                    "best_raw_edge_pct": row["best_raw_edge_pct"],
                    "threshold": row["raw_edge_threshold"],
                    "accepted": row["raw_edge_accepted"],
                    "rejection_reason": row["raw_edge_rejection_reason"],
                    "status": row["raw_edge_status"],
                },
            },
        )
        rows.append(row)

    return {
        "date": today.isoformat(),
        "total_games": len(games),
        "summary": _raw_board_summary(rows),
        "games": rows,
    }


@router.get("/raw-edge-board")
def raw_edge_board(db: Session = Depends(get_db)):
    return build_raw_edge_board(db)
