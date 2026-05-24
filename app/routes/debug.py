from __future__ import annotations

import logging
import calendar
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from scipy.stats import norm
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.config import DEBUG, ODDS_API_MONTHLY_QUOTA
from app.middleware.auth import verify_api_key
from app.middleware.limiter import limiter
from app.models.schema import EdgeResult, Game, GameOdds, GameOutcomeReview, LineMovement, OddsApiRequestLog, Prediction, SnapshotType
from app.scheduler import scheduler
from app.services.betting_policy import get_betting_profile, qualifies_for_bet_policy
from app.services.edge_service import (
    TOTAL_STD_DEV,
    calculate_all_edges_today,
    get_edge_persistence_failures,
    validate_active_edge_lineage,
)
from app.services.ev_math import american_to_decimal, calc_edge, implied_prob_raw, remove_vig
from app.services.market_respect_service import market_respect_adjustment, market_respect_for_edge
from app.services.decision_journal_service import build_daily_trade_summary, persist_tradable_decisions
from app.services.odds_service import odds_freshness_metadata
from app.services.sharp_move_journal_service import persist_sharp_move_journal
from app.services.totals_policy_service import totals_policy_backtest
from app.routes.ranked import _build_decision_queue

router = APIRouter(prefix="/api/debug", tags=["debug"])
logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
MIN_DIAGNOSTIC_KELLY_FRACTION = 0.001
RAW_EDGE_THRESHOLD = 0.0
DEBUG_NEAR_EDGE_THRESHOLD = -0.005


@router.get("/tradable-signals")
def tradable_signals_debug(
    limit: int = Query(50, ge=1, le=50),
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
):
    rows = _build_decision_queue(db=db, limit=limit, active_only=active_only)
    by_signal = Counter(row.get("tradable_signal") or "UNKNOWN" for row in rows)
    by_decision = Counter(row.get("decision_status") or "UNKNOWN" for row in rows)
    by_play_signal: dict[tuple[str, str], int] = Counter(
        ((row.get("play") or "unknown").lower(), row.get("tradable_signal") or "UNKNOWN")
        for row in rows
    )
    by_play_decision: dict[tuple[str, str], int] = Counter(
        ((row.get("play") or "unknown").lower(), row.get("decision_status") or "UNKNOWN")
        for row in rows
    )
    fire_rows = [row for row in rows if row.get("decision_status") == "FIRE"]
    watch_rows = [row for row in rows if row.get("decision_status") == "WATCH"]
    blocked_rows = [row for row in rows if row.get("decision_status") == "BLOCKED"]
    pass_rows = [row for row in rows if row.get("tradable_signal") == "PASS"]

    return {
        "status": "ok",
        "total": len(rows),
        "execution_counts": dict(by_decision),
        "tradable_signal_counts": dict(by_signal),
        "counts": dict(by_decision),
        "by_play_signal": [
            {"play": play, "tradable_signal": signal, "count": count}
            for (play, signal), count in sorted(by_play_signal.items(), key=lambda item: (item[0][0], item[0][1]))
        ],
        "by_play_decision": [
            {"play": play, "decision_status": decision, "count": count}
            for (play, decision), count in sorted(by_play_decision.items(), key=lambda item: (item[0][0], item[0][1]))
        ],
        "fire": fire_rows,
        "trade": fire_rows,
        "watch": watch_rows,
        "blocked": blocked_rows,
        "pass": pass_rows,
        "research_watch": [row for row in rows if row.get("tradable_signal") == "WATCH"],
    }


@router.get("/tradable-decision-journal")
def tradable_decision_journal_debug(
    limit: int = Query(50, ge=1, le=50),
    active_only: bool = Query(True),
    summary: bool = Query(False),
    db: Session = Depends(get_db),
):
    if summary:
        return build_daily_trade_summary(db=db, limit=limit, active_only=active_only)
    return persist_tradable_decisions(db=db, limit=limit, active_only=active_only)


@router.get("/sharp-move-journal")
def sharp_move_journal_debug(
    db: Session = Depends(get_db),
):
    return persist_sharp_move_journal(db=db)


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


def _graded_total_result(direction: str, actual_total: float, market_total: float) -> str:
    if actual_total == market_total:
        return "push"
    if direction == "under":
        return "win" if actual_total < market_total else "loss"
    if direction == "over":
        return "win" if actual_total > market_total else "loss"
    return "push"


def _profit_for_result(result: str, odds_american: int | None = None) -> float:
    result = (result or "").lower()
    if result == "push":
        return 0.0
    if result == "loss":
        return -1.0
    if result != "win":
        return 0.0
    if odds_american is None:
        return 100 / 110
    return american_to_decimal(int(odds_american)) - 1.0


def _aware_utc_from_start_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _odds_snapshot_summary(odds: GameOdds | None) -> dict | None:
    if odds is None:
        return None
    return {
        "id": odds.id,
        "sportsbook": odds.sportsbook,
        "fetched_at": _iso_datetime(odds.fetched_at),
        "away_ml": odds.away_ml,
        "home_ml": odds.home_ml,
        "total_line": float(odds.total_line) if odds.total_line is not None else None,
        "over_odds": odds.over_odds,
        "under_odds": odds.under_odds,
    }


def _latest_by_game(rows) -> dict[int, object]:
    latest: dict[int, object] = {}
    for row in rows:
        latest.setdefault(row.game_id, row)
    return latest


def build_market_readiness_report(db: Session) -> dict:
    today = datetime.now(ET).date()
    now_utc = datetime.now(timezone.utc)
    games = db.query(Game).filter(Game.game_date == today).order_by(Game.start_time.asc(), Game.game_id.asc()).all()
    game_ids = [game.game_id for game in games]

    open_by_game: dict[int, GameOdds] = {}
    pregame_by_game: dict[int, GameOdds] = {}
    movement_by_game: dict[int, LineMovement] = {}
    edge_by_game: dict[int, EdgeResult] = {}

    if game_ids:
        odds_rows = (
            db.query(GameOdds)
            .filter(GameOdds.game_id.in_(game_ids))
            .order_by(GameOdds.game_id.asc(), GameOdds.fetched_at.desc(), GameOdds.id.desc())
            .all()
        )
        open_by_game = _latest_by_game(row for row in odds_rows if row.snapshot_type == SnapshotType.open)
        pregame_by_game = _latest_by_game(row for row in odds_rows if row.snapshot_type == SnapshotType.pregame)

        movement_by_game = _latest_by_game(
            db.query(LineMovement)
            .filter(LineMovement.game_id.in_(game_ids))
            .order_by(LineMovement.game_id.asc(), LineMovement.calculated_at.desc(), LineMovement.id.desc())
            .all()
        )
        edge_by_game = _latest_by_game(
            db.query(EdgeResult)
            .filter(EdgeResult.game_id.in_(game_ids), EdgeResult.is_active.is_(True))
            .order_by(EdgeResult.game_id.asc(), EdgeResult.calculated_at.desc(), EdgeResult.id.desc())
            .all()
        )

    rows = []
    readiness_counts: Counter[str] = Counter()
    for game in games:
        open_odds = open_by_game.get(game.game_id)
        pregame_odds = pregame_by_game.get(game.game_id)
        movement = movement_by_game.get(game.game_id)
        edge = edge_by_game.get(game.game_id)
        start_utc = _aware_utc_from_start_time(game.start_time)
        minutes_to_start = round((start_utc - now_utc).total_seconds() / 60, 1) if start_utc else None
        pregame_due_at = start_utc - timedelta(minutes=45) if start_utc else None

        if pregame_odds and movement:
            readiness = "CLV_READY"
            reason = "Pregame snapshot and line movement are available."
        elif pregame_due_at and now_utc < pregame_due_at:
            readiness = "WAITING_FOR_PREGAME_WINDOW"
            reason = "Pregame snapshot is not due until 45 minutes before first pitch."
        elif not open_odds:
            readiness = "MISSING_OPEN_ODDS"
            reason = "No opening odds snapshot is stored for this game."
        elif not pregame_odds:
            readiness = "PREGAME_SNAPSHOT_MISSING"
            reason = "Pregame snapshot should be available before CLV can be trusted."
        elif not movement:
            readiness = "MOVEMENT_MISSING"
            reason = "Pregame odds exist, but line movement has not been computed."
        else:
            readiness = "UNKNOWN"
            reason = "Readiness could not be classified."

        readiness_counts[readiness] += 1
        rows.append({
            "game_id": game.game_id,
            "matchup": f"{game.away_team} @ {game.home_team}",
            "start_time": game.start_time,
            "minutes_to_start": minutes_to_start,
            "pregame_snapshot_due_at": _iso_datetime(pregame_due_at),
            "readiness": readiness,
            "readiness_reason": reason,
            "has_open_snapshot": open_odds is not None,
            "has_pregame_snapshot": pregame_odds is not None,
            "has_line_movement": movement is not None,
            "has_active_edge": edge is not None,
            "active_edge": {
                "id": edge.id,
                "run_stage": edge.run_stage,
                "play": edge.recommended_play,
                "edge_pct": float(edge.edge_pct or 0),
                "calculated_at": _iso_datetime(edge.calculated_at),
                "odds_snapshot_type": edge.odds_snapshot_type,
                "movement_id": edge.movement_id,
            } if edge else None,
            "open_snapshot": _odds_snapshot_summary(open_odds),
            "pregame_snapshot": _odds_snapshot_summary(pregame_odds),
            "line_movement": {
                "id": movement.id,
                "calculated_at": _iso_datetime(movement.calculated_at),
                "open_total": float(movement.open_total) if movement.open_total is not None else None,
                "pregame_total": float(movement.pregame_total) if movement.pregame_total is not None else None,
                "total_move": float(movement.total_move) if movement.total_move is not None else None,
                "sharp_home": bool(movement.sharp_home),
                "sharp_away": bool(movement.sharp_away),
                "total_steam_over": bool(movement.total_steam_over),
                "total_steam_under": bool(movement.total_steam_under),
            } if movement else None,
        })

    return {
        "status": "ok",
        "date": today.isoformat(),
        "quota": build_odds_quota_report(db),
        "total_games": len(games),
        "counts": dict(readiness_counts),
        "clv_ready_games": readiness_counts.get("CLV_READY", 0),
        "waiting_for_pregame_window": readiness_counts.get("WAITING_FOR_PREGAME_WINDOW", 0),
        "missing_open_odds": readiness_counts.get("MISSING_OPEN_ODDS", 0),
        "missing_pregame_snapshots": readiness_counts.get("PREGAME_SNAPSHOT_MISSING", 0),
        "missing_line_movement": readiness_counts.get("MOVEMENT_MISSING", 0),
        "games": rows,
    }


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_odds_quota_report(db: Session) -> dict:
    now_et = datetime.now(ET)
    month_start = now_et.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today = now_et.date()
    rows = (
        db.query(OddsApiRequestLog)
        .filter(OddsApiRequestLog.requested_at >= month_start.astimezone(timezone.utc))
        .order_by(OddsApiRequestLog.requested_at.desc(), OddsApiRequestLog.id.desc())
        .all()
    )
    ok_rows = [row for row in rows if row.status == "ok"]
    today_rows = [
        row for row in ok_rows
        if _as_utc(row.requested_at) and _as_utc(row.requested_at).astimezone(ET).date() == today
    ]
    by_snapshot = Counter(row.snapshot_type or "unknown" for row in ok_rows)
    used = len(ok_rows)
    remaining = max(ODDS_API_MONTHLY_QUOTA - used, 0)
    days_elapsed = max(today.day, 1)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    daily_average = used / days_elapsed
    projected_month_used = round(daily_average * days_in_month, 1)
    projected_over_cap = ODDS_API_MONTHLY_QUOTA > 0 and projected_month_used > ODDS_API_MONTHLY_QUOTA
    projected_warning = ODDS_API_MONTHLY_QUOTA > 0 and projected_month_used >= ODDS_API_MONTHLY_QUOTA * 0.9
    return {
        "month": month_start.strftime("%Y-%m"),
        "provider": "the_odds_api",
        "monthly_quota": ODDS_API_MONTHLY_QUOTA,
        "month_used": used,
        "month_remaining": remaining,
        "month_usage_pct": round(used / ODDS_API_MONTHLY_QUOTA, 4) if ODDS_API_MONTHLY_QUOTA else None,
        "today_used": len(today_rows),
        "daily_average": round(daily_average, 2),
        "projected_month_used": projected_month_used,
        "projected_remaining": round(ODDS_API_MONTHLY_QUOTA - projected_month_used, 1),
        "projected_over_cap": projected_over_cap,
        "projected_warning": projected_warning,
        "quota_alert": (
            "OVER_CAP_PROJECTION" if projected_over_cap else
            "NEAR_CAP_PROJECTION" if projected_warning else
            "OK"
        ),
        "errors_this_month": len([row for row in rows if row.status != "ok"]),
        "by_snapshot_type": dict(by_snapshot),
        "recent_requests": [
            {
                "requested_at": _iso_datetime(_as_utc(row.requested_at)),
                "snapshot_type": row.snapshot_type,
                "status": row.status,
                "events_returned": row.events_returned,
                "raw_bytes": row.raw_bytes,
                "bookmakers": row.bookmakers,
                "error": row.error,
            }
            for row in rows[:10]
        ],
    }


def _latest_rows_by_game(db: Session, game_ids: list[int], snapshot_type: SnapshotType) -> dict[int, GameOdds]:
    latest: dict[int, GameOdds] = {}
    if not game_ids:
        return latest
    rows = (
        db.query(GameOdds)
        .filter(GameOdds.game_id.in_(game_ids), GameOdds.snapshot_type == snapshot_type)
        .order_by(GameOdds.game_id.asc(), GameOdds.fetched_at.desc(), GameOdds.id.desc())
        .all()
    )
    for row in rows:
        latest.setdefault(row.game_id, row)
    return latest


def _latest_any_odds_by_game(db: Session, game_ids: list[int]) -> dict[int, GameOdds]:
    latest: dict[int, GameOdds] = {}
    if not game_ids:
        return latest
    rows = (
        db.query(GameOdds)
        .filter(GameOdds.game_id.in_(game_ids))
        .order_by(GameOdds.game_id.asc(), GameOdds.fetched_at.desc(), GameOdds.id.desc())
        .all()
    )
    for row in rows:
        latest.setdefault(row.game_id, row)
    return latest


def _play_odds_and_line(odds: GameOdds | None, play: str) -> tuple[int | None, float | None]:
    if odds is None:
        return None, None
    if play == "away_ml":
        return odds.away_ml, None
    if play == "home_ml":
        return odds.home_ml, None
    if play == "over":
        return odds.over_odds, float(odds.total_line) if odds.total_line is not None else None
    if play == "under":
        return odds.under_odds, float(odds.total_line) if odds.total_line is not None else None
    return None, None


def _edge_pct_for_play(edge: EdgeResult | None, play: str) -> float | None:
    if edge is None:
        return None
    if play == "away_ml":
        return float(edge.edge_away) if edge.edge_away is not None else None
    if play == "home_ml":
        return float(edge.edge_home) if edge.edge_home is not None else None
    if play == "over":
        return float(edge.total_edge or 0) / 100 if edge.total_edge is not None else None
    if play == "under":
        return -float(edge.total_edge or 0) / 100 if edge.total_edge is not None else None
    return None


def _ev_for_play(edge: EdgeResult | None, play: str) -> float | None:
    if edge is None:
        return None
    if play == "away_ml":
        return float(edge.ev_away) if edge.ev_away is not None else None
    if play == "home_ml":
        return float(edge.ev_home) if edge.ev_home is not None else None
    if play == "over":
        return float(edge.ev_over) if edge.ev_over is not None else None
    if play == "under":
        return float(edge.ev_under) if edge.ev_under is not None else None
    return None


def _clv_for_play(open_odds: GameOdds | None, close_odds: GameOdds | None, play: str) -> dict[str, float | None]:
    open_price, open_line = _play_odds_and_line(open_odds, play)
    close_price, close_line = _play_odds_and_line(close_odds, play)
    price_clv = None
    line_clv = None
    if open_price is not None and close_price is not None:
        price_clv = round(implied_prob_raw(close_price) - implied_prob_raw(open_price), 4)
    if play == "over" and open_line is not None and close_line is not None:
        line_clv = round(close_line - open_line, 2)
    elif play == "under" and open_line is not None and close_line is not None:
        line_clv = round(open_line - close_line, 2)
    return {"price_clv": price_clv, "line_clv": line_clv}


def _movement_bucket_for_play(movement: LineMovement | None, play: str) -> str:
    if movement is None:
        return "no_movement"
    if play in {"over", "under"}:
        total = abs(float(movement.total_move or 0))
        if total >= 0.5:
            return "total_steam"
        if total >= 0.2:
            return "minor_move"
        return "flat"
    side_value = movement.away_prob_move if play == "away_ml" else movement.home_prob_move
    side_move = abs(float(side_value or 0))
    if side_move >= 0.04:
        return "ml_steam"
    if side_move >= 0.02:
        return "minor_move"
    return "flat"


def _warehouse_play_row(
    *,
    game: Game,
    play: str,
    open_odds: GameOdds | None,
    pregame_odds: GameOdds | None,
    latest_odds: GameOdds | None,
    movement: LineMovement | None,
    edge: EdgeResult | None,
    respect: dict | None,
) -> dict:
    open_price, open_line = _play_odds_and_line(open_odds, play)
    pregame_price, pregame_line = _play_odds_and_line(pregame_odds, play)
    latest_price, latest_line = _play_odds_and_line(latest_odds, play)
    clv = _clv_for_play(open_odds, pregame_odds, play)
    return {
        "game_id": game.game_id,
        "matchup": f"{game.away_team} @ {game.home_team}",
        "start_time": game.start_time,
        "play": play,
        "recommended": bool(edge and edge.recommended_play == play),
        "edge_pct": _edge_pct_for_play(edge, play),
        "ev": _ev_for_play(edge, play),
        "open_line": open_line,
        "open_price": open_price,
        "open_fetched_at": _iso_datetime(open_odds.fetched_at) if open_odds else None,
        "pregame_line": pregame_line,
        "pregame_price": pregame_price,
        "pregame_fetched_at": _iso_datetime(pregame_odds.fetched_at) if pregame_odds else None,
        "latest_line": latest_line,
        "latest_price": latest_price,
        "latest_snapshot_type": latest_odds.snapshot_type.value if latest_odds and latest_odds.snapshot_type else None,
        "latest_fetched_at": _iso_datetime(latest_odds.fetched_at) if latest_odds else None,
        "line_clv": clv["line_clv"],
        "price_clv": clv["price_clv"],
        "movement_bucket": _movement_bucket_for_play(movement, play),
        "movement_direction": edge.movement_direction if edge and edge.recommended_play == play else None,
        "market_respect_score": respect.get("score") if respect and edge and edge.recommended_play == play else None,
        "market_respect_tags": respect.get("tags") if respect and edge and edge.recommended_play == play else [],
        "readiness": (
            "CLV_READY" if pregame_odds and movement else
            "PREGAME_SNAPSHOT_MISSING" if not pregame_odds else
            "MOVEMENT_MISSING"
        ),
    }


def build_odds_warehouse_report(db: Session) -> dict:
    today = datetime.now(ET).date()
    games = db.query(Game).filter(Game.game_date == today).order_by(Game.start_time.asc(), Game.game_id.asc()).all()
    game_ids = [game.game_id for game in games]
    open_by_game = _latest_rows_by_game(db, game_ids, SnapshotType.open)
    pregame_by_game = _latest_rows_by_game(db, game_ids, SnapshotType.pregame)
    latest_by_game = _latest_any_odds_by_game(db, game_ids)
    movement_by_game = _latest_by_game(
        db.query(LineMovement)
        .filter(LineMovement.game_id.in_(game_ids))
        .order_by(LineMovement.game_id.asc(), LineMovement.calculated_at.desc(), LineMovement.id.desc())
        .all()
    ) if game_ids else {}
    edge_by_game = _latest_by_game(
        db.query(EdgeResult)
        .filter(EdgeResult.game_id.in_(game_ids), EdgeResult.is_active.is_(True))
        .order_by(EdgeResult.game_id.asc(), EdgeResult.calculated_at.desc(), EdgeResult.id.desc())
        .all()
    ) if game_ids else {}

    rows = []
    play_rows = []
    for game in games:
        open_odds = open_by_game.get(game.game_id)
        pregame_odds = pregame_by_game.get(game.game_id)
        latest_odds = latest_by_game.get(game.game_id)
        movement = movement_by_game.get(game.game_id)
        edge = edge_by_game.get(game.game_id)
        respect = market_respect_for_edge(db, edge, odds=open_odds, game=game) if edge else None
        components = respect.get("components", {}) if respect else {}
        for play in ("away_ml", "home_ml", "over", "under"):
            play_rows.append(
                _warehouse_play_row(
                    game=game,
                    play=play,
                    open_odds=open_odds,
                    pregame_odds=pregame_odds,
                    latest_odds=latest_odds,
                    movement=movement,
                    edge=edge,
                    respect=respect,
                )
            )
        rows.append({
            "game_id": game.game_id,
            "matchup": f"{game.away_team} @ {game.home_team}",
            "start_time": game.start_time,
            "play": edge.recommended_play if edge else None,
            "recommended_play": edge.recommended_play if edge else None,
            "run_stage": edge.run_stage if edge else None,
            "edge_pct": float(edge.edge_pct or 0) if edge else None,
            "market_respect_score": respect.get("score") if respect else None,
            "market_respect_tags": respect.get("tags") if respect else [],
            "line_clv": components.get("line_clv"),
            "price_clv": components.get("price_clv"),
            "open": _odds_snapshot_summary(open_odds),
            "pregame": _odds_snapshot_summary(pregame_odds),
            "movement": {
                "calculated_at": _iso_datetime(movement.calculated_at),
                "total_move": float(movement.total_move) if movement and movement.total_move is not None else None,
                "home_prob_move": float(movement.home_prob_move) if movement and movement.home_prob_move is not None else None,
                "away_prob_move": float(movement.away_prob_move) if movement and movement.away_prob_move is not None else None,
                "sharp_home": bool(movement.sharp_home),
                "sharp_away": bool(movement.sharp_away),
                "total_steam_over": bool(movement.total_steam_over),
                "total_steam_under": bool(movement.total_steam_under),
            } if movement else None,
            "readiness": (
                "CLV_READY" if pregame_odds and movement else
                "PREGAME_SNAPSHOT_MISSING" if not pregame_odds else
                "MOVEMENT_MISSING"
            ),
        })

    return {
        "status": "ok",
        "date": today.isoformat(),
        "quota": build_odds_quota_report(db),
        "summary": {
            "games": len(rows),
            "play_rows": len(play_rows),
            "open_snapshots": sum(1 for row in rows if row["open"]),
            "pregame_snapshots": sum(1 for row in rows if row["pregame"]),
            "clv_ready": sum(1 for row in rows if row["readiness"] == "CLV_READY"),
            "clv_ready_play_rows": sum(1 for row in play_rows if row["readiness"] == "CLV_READY"),
        },
        "games": rows,
        "plays": play_rows,
    }


def _total_play_odds(play: str, edge: EdgeResult | None, odds: GameOdds | None) -> int | None:
    if play == "over":
        return edge.over_odds if edge and edge.over_odds is not None else (odds.over_odds if odds else None)
    if play == "under":
        return edge.under_odds if edge and edge.under_odds is not None else (odds.under_odds if odds else None)
    return None


def _closing_total_for_edge(
    db: Session,
    *,
    review: GameOutcomeReview,
    edge: EdgeResult | None,
    odds: GameOdds | None,
    movement: LineMovement | None,
) -> float | None:
    if movement and movement.pregame_total is not None:
        return float(movement.pregame_total)

    sportsbook = edge.sportsbook if edge and edge.sportsbook else (odds.sportsbook if odds else None)
    query = db.query(GameOdds).filter(
        GameOdds.game_id == review.game_id,
        GameOdds.snapshot_type == SnapshotType.pregame,
        GameOdds.total_line.isnot(None),
    )
    if sportsbook:
        same_book = (
            query.filter(GameOdds.sportsbook == sportsbook)
            .order_by(GameOdds.fetched_at.desc(), GameOdds.id.desc())
            .first()
        )
        if same_book:
            return float(same_book.total_line)
    close = query.order_by(GameOdds.fetched_at.desc(), GameOdds.id.desc()).first()
    return float(close.total_line) if close and close.total_line is not None else None


def _totals_segment_stats(rows: list[dict]) -> dict:
    wins = sum(1 for row in rows if row["result"] == "win")
    losses = sum(1 for row in rows if row["result"] == "loss")
    pushes = sum(1 for row in rows if row["result"] == "push")
    decisions = wins + losses
    profit_units = round(sum(row["profit_units"] for row in rows), 4)
    clv_rows = [row for row in rows if row.get("line_clv") is not None]
    deltas = [row["projected_total_minus_market"] for row in rows if row.get("projected_total_minus_market") is not None]
    actual_deltas = [row["actual_total_minus_market"] for row in rows if row.get("actual_total_minus_market") is not None]
    return {
        "count": len(rows),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate": round(wins / decisions, 4) if decisions else None,
        "profit_units": profit_units,
        "roi_per_bet": round(profit_units / len(rows), 4) if rows else 0.0,
        "avg_projected_total_minus_market": round(sum(deltas) / len(deltas), 3) if deltas else None,
        "avg_actual_total_minus_market": round(sum(actual_deltas) / len(actual_deltas), 3) if actual_deltas else None,
        "avg_line_clv": round(sum(row["line_clv"] for row in clv_rows) / len(clv_rows), 3) if clv_rows else None,
        "beat_close_rate": round(sum(1 for row in clv_rows if row["line_clv"] > 0) / len(clv_rows), 4) if clv_rows else None,
    }


def _distribution(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "avg": None, "min": None, "p25": None, "median": None, "p75": None, "max": None}
    ordered = sorted(values)

    def pick(percentile: float) -> float:
        idx = round((len(ordered) - 1) * percentile)
        return round(ordered[idx], 3)

    return {
        "count": len(ordered),
        "avg": round(sum(ordered) / len(ordered), 3),
        "min": round(ordered[0], 3),
        "p25": pick(0.25),
        "median": pick(0.5),
        "p75": pick(0.75),
        "max": round(ordered[-1], 3),
    }


def _delta_bucket(delta: float) -> str:
    if delta <= -2.0:
        return "model_2plus_runs_lower"
    if delta <= -1.0:
        return "model_1_to_2_runs_lower"
    if delta < -0.25:
        return "model_0_25_to_1_run_lower"
    if delta <= 0.25:
        return "near_market"
    if delta < 1.0:
        return "model_0_25_to_1_run_higher"
    if delta < 2.0:
        return "model_1_to_2_runs_higher"
    return "model_2plus_runs_higher"


def build_totals_bias_report(db: Session, *, min_sample: int = 5) -> dict:
    rows = (
        db.query(GameOutcomeReview, EdgeResult, GameOdds, LineMovement, Game)
        .outerjoin(EdgeResult, EdgeResult.id == GameOutcomeReview.edge_result_id)
        .outerjoin(GameOdds, GameOdds.id == EdgeResult.odds_id)
        .outerjoin(LineMovement, LineMovement.id == EdgeResult.movement_id)
        .outerjoin(Game, Game.game_id == GameOutcomeReview.game_id)
        .filter(
            GameOutcomeReview.model_total.isnot(None),
            GameOutcomeReview.book_total.isnot(None),
            GameOutcomeReview.final_away_score.isnot(None),
            GameOutcomeReview.final_home_score.isnot(None),
        )
        .order_by(GameOutcomeReview.game_date.desc(), GameOutcomeReview.id.desc())
        .all()
    )

    model_direction_rows: list[dict] = []
    recommended_total_rows: list[dict] = []
    by_model_direction: dict[str, list[dict]] = defaultdict(list)
    by_recommended_direction: dict[str, list[dict]] = defaultdict(list)
    by_delta_bucket: dict[str, list[dict]] = defaultdict(list)
    samples: list[dict] = []

    for review, edge, odds, movement, game in rows:
        model_total = float(review.model_total)
        market_total = float(review.book_total)
        actual_total = float(review.final_away_score + review.final_home_score)
        projected_minus_market = round(model_total - market_total, 3)
        actual_minus_market = round(actual_total - market_total, 3)
        if projected_minus_market < 0:
            model_direction = "under"
        elif projected_minus_market > 0:
            model_direction = "over"
        else:
            model_direction = "neutral"

        if model_direction in {"under", "over"}:
            result = _graded_total_result(model_direction, actual_total, market_total)
            close_total = _closing_total_for_edge(db, review=review, edge=edge, odds=odds, movement=movement)
            line_clv = None
            if close_total is not None:
                line_clv = round(close_total - market_total, 3) if model_direction == "over" else round(market_total - close_total, 3)
            row = {
                "game_id": review.game_id,
                "game_date": review.game_date.isoformat(),
                "matchup": f"{game.away_team} @ {game.home_team}" if game else None,
                "direction": model_direction,
                "result": result,
                "profit_units": _profit_for_result(result),
                "model_total": model_total,
                "market_total": market_total,
                "actual_total": actual_total,
                "projected_total_minus_market": projected_minus_market,
                "actual_total_minus_market": actual_minus_market,
                "line_clv": line_clv,
                "recommended_play": review.recommended_play,
                "bet_result": review.bet_result,
            }
            model_direction_rows.append(row)
            by_model_direction[model_direction].append(row)
            by_delta_bucket[_delta_bucket(projected_minus_market)].append(row)
            if len(samples) < 10:
                samples.append(row)

        play = (review.recommended_play or "").lower()
        if play in {"over", "under"} and (review.bet_result or "").lower() in {"win", "loss", "push"}:
            close_total = _closing_total_for_edge(db, review=review, edge=edge, odds=odds, movement=movement)
            line_clv = None
            if close_total is not None:
                line_clv = round(close_total - market_total, 3) if play == "over" else round(market_total - close_total, 3)
            recommended = {
                "game_id": review.game_id,
                "game_date": review.game_date.isoformat(),
                "matchup": f"{game.away_team} @ {game.home_team}" if game else None,
                "direction": play,
                "result": review.bet_result,
                "profit_units": _profit_for_result(review.bet_result, _total_play_odds(play, edge, odds)),
                "model_total": model_total,
                "market_total": market_total,
                "actual_total": actual_total,
                "projected_total_minus_market": projected_minus_market,
                "actual_total_minus_market": actual_minus_market,
                "line_clv": line_clv,
                "edge_pct": float(review.edge_pct or 0) if review.edge_pct is not None else None,
                "ev": float(review.ev or 0) if review.ev is not None else None,
            }
            recommended_total_rows.append(recommended)
            by_recommended_direction[play].append(recommended)

    direction_stats = {
        direction: _totals_segment_stats(items)
        for direction, items in sorted(by_model_direction.items())
    }
    recommended_stats = {
        direction: _totals_segment_stats(items)
        for direction, items in sorted(by_recommended_direction.items())
    }
    bucket_stats = {
        bucket: _totals_segment_stats(items)
        for bucket, items in sorted(by_delta_bucket.items())
        if len(items) >= min_sample
    }
    deltas = [row["projected_total_minus_market"] for row in model_direction_rows]
    under_count = len(by_model_direction.get("under", []))
    over_count = len(by_model_direction.get("over", []))
    total_direction_count = under_count + over_count
    under_stats = direction_stats.get("under", _totals_segment_stats([]))
    recommended_under_stats = recommended_stats.get("under", _totals_segment_stats([]))
    recommended_over_stats = recommended_stats.get("over", _totals_segment_stats([]))

    indicators: list[str] = []
    if total_direction_count and under_count / total_direction_count >= 0.65:
        indicators.append("under_edges_dominate")
    if under_stats["win_rate"] is not None and under_stats["win_rate"] >= 0.53:
        indicators.append("under_direction_beating_market_total")
    if under_stats["win_rate"] is not None and under_stats["win_rate"] < 0.5:
        indicators.append("model_likely_too_low_on_totals")
    if recommended_under_stats["roi_per_bet"] > 0:
        indicators.append("recommended_unders_profitable")
    if recommended_under_stats["roi_per_bet"] < 0 and recommended_under_stats["count"] >= min_sample:
        indicators.append("recommended_unders_unprofitable")
    if recommended_over_stats["count"] >= min_sample and recommended_over_stats["roi_per_bet"] > recommended_under_stats["roi_per_bet"]:
        indicators.append("overs_outperform_recommended_unders")

    conclusion = "insufficient_totals_history"
    if "under_edges_dominate" in indicators and "under_direction_beating_market_total" in indicators and "recommended_unders_profitable" in indicators:
        conclusion = "under_edge_has_historical_support"
    elif "under_edges_dominate" in indicators and (
        "model_likely_too_low_on_totals" in indicators or "recommended_unders_unprofitable" in indicators
    ):
        conclusion = "totals_model_biased_low"
    elif total_direction_count >= min_sample:
        conclusion = "mixed_totals_signal"

    return {
        "status": "ok",
        "min_sample": min_sample,
        "summary": {
            "reviewed_games": len(rows),
            "model_total_direction_games": len(model_direction_rows),
            "under_edge_count": under_count,
            "over_edge_count": over_count,
            "under_edge_share": round(under_count / total_direction_count, 4) if total_direction_count else None,
            "realized_under_winrate": under_stats["win_rate"],
            "recommended_totals_bets": len(recommended_total_rows),
            "recommended_under_roi": recommended_under_stats["roi_per_bet"],
            "recommended_over_roi": recommended_over_stats["roi_per_bet"],
            "projected_total_minus_market": _distribution(deltas),
            "conclusion": conclusion,
            "indicators": indicators,
        },
        "model_direction_results": direction_stats,
        "recommended_totals_roi_by_direction": recommended_stats,
        "projected_total_minus_market_buckets": bucket_stats,
        "recent_samples": samples,
    }


@router.get("/totals-bias")
def totals_bias(min_sample: int = Query(5, ge=1, le=100), db: Session = Depends(get_db)):
    return build_totals_bias_report(db, min_sample=min_sample)


def build_totals_policy_report(db: Session, *, min_sample: int = 5) -> dict:
    from app.routes.ranked import _build_ranked_rows

    current = _build_ranked_rows(db=db, limit=50, active_only=True)
    totals = [row for row in current if (row.get("play") or "").lower() in {"over", "under"}]
    by_status: dict[str, list[dict]] = defaultdict(list)
    reason_counts: Counter[str] = Counter()
    for row in totals:
        status = row.get("policy_status") or "UNKNOWN"
        by_status[status].append(row)
        for reason in row.get("policy_reasons") or []:
            reason_counts[reason] += 1
    cluster = current[0].get("totals_cluster") if current else {"under_share": 0.0, "warning": None, "penalized": 0}

    def _policy_play_payload(row: dict) -> dict:
        return {
            "rank": row.get("rank"),
            "game_id": row.get("game_id"),
            "matchup": row.get("matchup"),
            "play": row.get("play"),
            "adjusted_edge_pct": row.get("adjusted_edge_pct"),
            "totals_policy_score": row.get("totals_policy_score"),
            "policy_status": row.get("policy_status"),
            "policy_reason": row.get("policy_reason"),
            "market_respect_score": row.get("market_respect_score"),
            "market_respect_tags": row.get("market_respect_tags"),
            "alert_allowed": row.get("totals_policy_alert_allowed"),
        }

    actionable = [
        _policy_play_payload(row)
        for row in totals
        if row.get("policy_status") == "APPROVED"
        and row.get("totals_policy_alert_allowed")
    ]
    watchlist = [
        _policy_play_payload(row)
        for row in totals
        if row.get("policy_status") in {"CAUTION", "CLUSTER_RISK"}
    ]
    blocked = [
        {
            "rank": row.get("rank"),
            "game_id": row.get("game_id"),
            "matchup": row.get("matchup"),
            "play": row.get("play"),
            "totals_policy_score": row.get("totals_policy_score"),
            "policy_status": row.get("policy_status"),
            "policy_reasons": row.get("policy_reasons"),
            "policy_reason": row.get("policy_reason"),
        }
        for row in totals
        if row.get("policy_status") == "BLOCKED"
    ][:10]
    return {
        "status": "ok",
        "current_board": {
            "total_ranked_plays": len(current),
            "totals_plays": len(totals),
            "under_count": sum(1 for row in totals if row.get("play") == "under"),
            "over_count": sum(1 for row in totals if row.get("play") == "over"),
            "cluster": cluster,
            "blocked_counts": {status: len(rows) for status, rows in sorted(by_status.items())},
            "filter_reasons": [{"reason": reason, "count": count} for reason, count in reason_counts.most_common(10)],
            "actionable_plays": actionable[:15],
            "watchlist_plays": watchlist[:15],
            "actionable_or_watchlist": (actionable + watchlist)[:15],
            "sample_blocked_plays": blocked,
        },
        "backtest": totals_policy_backtest(db, min_sample=min_sample),
    }


@router.get("/totals-policy")
def totals_policy(min_sample: int = Query(5, ge=1, le=100), db: Session = Depends(get_db)):
    return build_totals_policy_report(db, min_sample=min_sample)


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


@router.get("/market-readiness")
def market_readiness(db: Session = Depends(get_db)):
    return build_market_readiness_report(db)


@router.get("/odds-warehouse")
def odds_warehouse(db: Session = Depends(get_db)):
    return build_odds_warehouse_report(db)


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
