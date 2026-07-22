from functools import lru_cache
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.middleware.timing import get_slow_endpoint_events
from app.models.schema import Game, Prediction
from app.routes.debug import build_market_readiness_report
from app.routes.edges import get_cached_today_edges
from app.routes.ranked import get_cached_decision_queue, get_cached_ranked_rows
from app.services.report_snapshot_service import get_report_snapshot

router = APIRouter(tags=["dashboard"])
ET = ZoneInfo("America/New_York")
LIVE_RESEARCH_MAX_AGE_SECONDS = 30 * 60
LIVE_RESEARCH_REPORTS = {"odds_warehouse", "totals_policy", "bullpen_today"}
DAILY_PIPELINE_DUE_TIME = time(10, 45)
RESEARCH_SNAPSHOT_DUE_TIME = time(11, 10)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


@lru_cache(maxsize=4)
def _load_template(name: str) -> str:
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=_load_template("dashboard.html"))


@router.get("/system", response_class=HTMLResponse)
def system_dashboard():
    return HTMLResponse(content=_load_template("system.html"))


@router.get("/simulator", response_class=HTMLResponse)
def simulator():
    return HTMLResponse(content=_load_template("simulator.html"))


@router.get("/bets", response_class=HTMLResponse)
def bets_dashboard():
    return HTMLResponse(content=_load_template("bets.html"))


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard():
    return HTMLResponse(content=_load_template("admin.html"))


def _dashboard_today() -> date:
    return datetime.now(ET).date()


@router.get("/api/dashboard/live")
def dashboard_live(db: Session = Depends(get_db)):
    today = _dashboard_today()
    return {
        "status": "ok",
        "date": today.isoformat(),
        "market_readiness": build_market_readiness_report(db),
        "decision_queue": get_cached_decision_queue(db=db, limit=20, active_only=True),
        "ranked_bets": get_cached_ranked_rows(db=db, limit=10, active_only=True),
        "snapshots": {
            "decision_queue": (get_report_snapshot(db, "decision_queue", report_date=today, max_age_seconds=None) or {}).get("snapshot"),
            "ranked_rows": (get_report_snapshot(db, "ranked_rows", report_date=today, max_age_seconds=None) or {}).get("snapshot"),
        },
    }


@router.get("/api/dashboard/research")
def dashboard_research(db: Session = Depends(get_db)):
    today = _dashboard_today()

    def snapshot_for(name: str) -> dict | None:
        if name in LIVE_RESEARCH_REPORTS:
            return get_report_snapshot(
                db,
                name,
                report_date=today,
                max_age_seconds=LIVE_RESEARCH_MAX_AGE_SECONDS,
            )
        return get_report_snapshot(db, name, report_date=today)

    reports = {
        name: snapshot_for(name)
        for name in (
            "odds_warehouse",
            "totals_policy",
            "paper_clv",
            "movement_report",
            "profitability_report",
            "profitability_report_min1",
            "bullpen_today",
            "performance_summary",
        )
    }
    return {
        "status": "ok",
        "date": today.isoformat(),
        "reports": reports,
        "missing": [name for name, payload in reports.items() if payload is None],
    }


@router.get("/api/dashboard/slow-endpoints")
def dashboard_slow_endpoints(limit: int = 20):
    return {
        "status": "ok",
        "limit": limit,
        "events": get_slow_endpoint_events(limit=max(1, min(limit, 80))),
    }


def _snapshot_age_seconds(snapshot: dict | None) -> int | None:
    generated_at = (snapshot or {}).get("generated_at")
    if not generated_at:
        return None
    try:
        value = datetime.fromisoformat(generated_at)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - value).total_seconds()))


def _health_status(checks: list[dict]) -> str:
    if any(check["status"] == "BROKEN" for check in checks):
        return "BROKEN"
    if any(check["status"] == "WARNING" for check in checks):
        return "WARNING"
    if any(check["status"] == "PRE-RUN" for check in checks):
        return "PRE-RUN"
    return "OK"


def _before_time(now: datetime, due_time: time) -> bool:
    return now.timetz().replace(tzinfo=None) < due_time


def _due_label(due_time: time) -> str:
    hour = due_time.hour
    minute = due_time.minute
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour if 1 <= hour <= 12 else abs(hour - 12)
    if display_hour == 0:
        display_hour = 12
    return f"{display_hour}:{minute:02d} {suffix} ET"


@router.get("/api/dashboard/health")
def dashboard_health(db: Session = Depends(get_db)):
    today = _dashboard_today()
    now_et = datetime.now(ET)
    before_pipeline_due = _before_time(now_et, DAILY_PIPELINE_DUE_TIME)
    before_research_due = _before_time(now_et, RESEARCH_SNAPSHOT_DUE_TIME)
    readiness = build_market_readiness_report(db)
    quota = readiness.get("quota") or {}
    integrity = readiness.get("integrity") or {}
    waiting_for_pregame_window = int(readiness.get("waiting_for_pregame_window") or 0)
    total_games = readiness.get("total_games") or len(readiness.get("games") or [])
    slow_events = get_slow_endpoint_events(limit=10)

    decision_snapshot = (get_report_snapshot(db, "decision_queue", report_date=today, max_age_seconds=None) or {}).get("snapshot")
    ranked_snapshot = (get_report_snapshot(db, "ranked_rows", report_date=today, max_age_seconds=None) or {}).get("snapshot")
    research_names = (
        "odds_warehouse",
        "totals_policy",
        "paper_clv",
        "movement_report",
        "profitability_report",
        "profitability_report_min1",
        "bullpen_today",
    )
    live_research_names = tuple(sorted(LIVE_RESEARCH_REPORTS))
    historical_research_names = tuple(name for name in research_names if name not in LIVE_RESEARCH_REPORTS)
    live_research_snapshots = {
        name: (get_report_snapshot(db, name, report_date=today, max_age_seconds=LIVE_RESEARCH_MAX_AGE_SECONDS) or {}).get("snapshot")
        for name in live_research_names
    }
    historical_research_snapshots = {
        name: (get_report_snapshot(db, name, report_date=today, max_age_seconds=None) or {}).get("snapshot")
        for name in historical_research_names
    }
    research_snapshots = {
        **live_research_snapshots,
        **historical_research_snapshots,
    }
    decision_age = _snapshot_age_seconds(decision_snapshot)
    live_research_ages = [_snapshot_age_seconds(snapshot) for snapshot in live_research_snapshots.values() if snapshot]
    live_research_age = max(live_research_ages) if live_research_ages else None
    missing_live_research = [name for name, snapshot in live_research_snapshots.items() if snapshot is None]
    research_ages = [_snapshot_age_seconds(snapshot) for snapshot in research_snapshots.values() if snapshot]
    oldest_research_age = max(research_ages) if research_ages else None
    missing_research = [name for name, snapshot in research_snapshots.items() if snapshot is None]

    checks = []
    provider_paused = bool(quota.get("provider_out_of_usage_credits"))
    checks.append({
        "name": "Odds Feed",
        "status": "BROKEN" if provider_paused else ("WARNING" if quota.get("errors_this_month") else "OK"),
        "detail": quota.get("latest_provider_message") or quota.get("quota_alert") or quota.get("provider_status") or "Provider OK",
    })
    clv_usable = integrity.get("clv_usable") or 0
    clv_status = "OK" if total_games and clv_usable else ("WARNING" if total_games else "BROKEN")
    clv_detail = f"{clv_usable}/{total_games} games CLV usable" if total_games else "No games loaded"
    if total_games and not clv_usable and before_pipeline_due:
        clv_status = "PRE-RUN"
        clv_detail = f"Pregame CLV not due until {_due_label(DAILY_PIPELINE_DUE_TIME)}"
    elif total_games and not clv_usable and waiting_for_pregame_window == total_games:
        clv_status = "PRE-RUN"
        clv_detail = f"Pregame CLV windows not due yet; {waiting_for_pregame_window}/{total_games} games waiting"
    elif total_games and waiting_for_pregame_window and not clv_usable:
        clv_status = "WARNING"
        clv_detail = f"0/{total_games} games CLV usable; {waiting_for_pregame_window} still waiting for pregame windows"
    checks.append({
        "name": "Pregame CLV",
        "status": clv_status,
        "detail": clv_detail,
    })
    decision_status = "OK" if decision_age is not None and decision_age <= 3600 else ("WARNING" if decision_age is not None else "BROKEN")
    decision_detail = f"Snapshot age {decision_age // 60}m" if decision_age is not None else "No decision snapshot"
    if decision_age is None and before_pipeline_due:
        decision_status = "PRE-RUN"
        decision_detail = f"Decision snapshot not due until {_due_label(DAILY_PIPELINE_DUE_TIME)}"
    checks.append({
        "name": "Decision Queue",
        "status": decision_status,
        "detail": decision_detail,
    })
    research_status = "OK" if not missing_live_research and live_research_age is not None else ("WARNING" if live_research_age is not None else "BROKEN")
    research_detail = (
        f"Live age {live_research_age // 60}m; historical oldest {oldest_research_age // 60}m"
        if live_research_age is not None and oldest_research_age is not None
        else f"Missing {len(missing_live_research)} live reports"
    )
    if missing_live_research and live_research_age is None and before_research_due:
        research_status = "PRE-RUN"
        research_detail = f"Live research snapshots not due until {_due_label(RESEARCH_SNAPSHOT_DUE_TIME)}"
    checks.append({
        "name": "Research Snapshots",
        "status": research_status,
        "detail": research_detail,
    })
    checks.append({
        "name": "Endpoint Speed",
        "status": "WARNING" if slow_events else "OK",
        "detail": f"{len(slow_events)} recent slow endpoints" if slow_events else "No recent slow endpoints",
    })

    return {
        "status": _health_status(checks),
        "date": today.isoformat(),
        "checks": checks,
        "summary": {
            "total_games": total_games,
            "clv_usable": clv_usable,
            "waiting_for_pregame_window": waiting_for_pregame_window,
            "pre_run": before_pipeline_due,
            "daily_pipeline_due_time": _due_label(DAILY_PIPELINE_DUE_TIME),
            "research_snapshot_due_time": _due_label(RESEARCH_SNAPSHOT_DUE_TIME),
            "decision_snapshot_age_seconds": decision_age,
            "ranked_snapshot_age_seconds": _snapshot_age_seconds(ranked_snapshot),
            "live_research_snapshot_age_seconds": live_research_age,
            "oldest_research_snapshot_age_seconds": oldest_research_age,
            "missing_research_reports": missing_research,
            "missing_live_research_reports": missing_live_research,
            "slow_endpoint_count": len(slow_events),
        },
    }


def _game_payload(game: Game) -> dict:
    return {
        "game_id": game.game_id,
        "game_date": game.game_date.isoformat() if game.game_date else None,
        "season": game.season,
        "away_team": game.away_team,
        "home_team": game.home_team,
        "away_team_id": game.away_team_id,
        "home_team_id": game.home_team_id,
        "venue": game.venue,
        "status": game.status,
        "start_time": game.start_time,
        "away_probable_pitcher": game.away_probable_pitcher,
        "home_probable_pitcher": game.home_probable_pitcher,
        "final_away_score": game.final_away_score,
        "final_home_score": game.final_home_score,
    }


def _prediction_payload(prediction: Prediction) -> dict:
    return {
        "game_id": prediction.game_id,
        "model_version": prediction.model_version,
        "away_win_pct": prediction.away_win_pct,
        "home_win_pct": prediction.home_win_pct,
        "projected_away_score": prediction.projected_away_score,
        "projected_home_score": prediction.projected_home_score,
        "projected_total": prediction.projected_total,
        "confidence_score": prediction.confidence_score,
        "recommended_side": prediction.recommended_side,
        "kbb_adv": prediction.kbb_adv,
        "park_factor_adv": prediction.park_factor_adv,
        "pythagorean_win_pct_adv": prediction.pythagorean_win_pct_adv,
        "home_starter_xera": prediction.home_starter_xera,
        "away_starter_xera": prediction.away_starter_xera,
        "using_xera": prediction.using_xera,
    }


@router.get("/api/dashboard/intel")
def dashboard_intel(db: Session = Depends(get_db)):
    today = datetime.now(ET).date()
    games = (
        db.query(Game)
        .filter(Game.game_date == today)
        .order_by(Game.start_time.asc(), Game.game_id.asc())
        .all()
    )
    subq = (
        db.query(
            Prediction.game_id,
            func.max(Prediction.prediction_id).label("max_id"),
        )
        .join(Game, Prediction.game_id == Game.game_id)
        .filter(Game.game_date == today, Prediction.is_active == True)  # noqa: E712
        .group_by(Prediction.game_id)
        .subquery()
    )
    predictions = (
        db.query(Prediction)
        .join(subq, Prediction.prediction_id == subq.c.max_id)
        .all()
    )
    edge_snapshot = get_report_snapshot(db, "today_edges", report_date=today, max_age_seconds=3600)
    edges = edge_snapshot["rows"] if edge_snapshot and isinstance(edge_snapshot.get("rows"), list) else get_cached_today_edges(db)
    return {
        "status": "ok",
        "date": today.isoformat(),
        "games": [_game_payload(game) for game in games],
        "edges": edges,
        "predictions": [_prediction_payload(prediction) for prediction in predictions],
        "snapshots": {
            "today_edges": (edge_snapshot or {}).get("snapshot"),
        },
    }
