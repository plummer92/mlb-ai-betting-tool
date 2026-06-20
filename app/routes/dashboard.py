from functools import lru_cache
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.middleware.timing import get_slow_endpoint_events
from app.routes.debug import build_market_readiness_report
from app.routes.ranked import get_cached_decision_queue, get_cached_ranked_rows
from app.services.report_snapshot_service import get_report_snapshot

router = APIRouter(tags=["dashboard"])

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


@router.get("/api/dashboard/live")
def dashboard_live(db: Session = Depends(get_db)):
    today = date.today()
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
    today = date.today()
    reports = {
        name: get_report_snapshot(db, name, report_date=today)
        for name in (
            "odds_warehouse",
            "totals_policy",
            "paper_clv",
            "movement_report",
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
