from functools import lru_cache
from datetime import date, datetime
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
