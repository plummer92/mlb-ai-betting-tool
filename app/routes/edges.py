from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.edge_service import get_trustworthy_active_edges

ET = ZoneInfo("America/New_York")

router = APIRouter(prefix="/api/edges", tags=["edges"])


@router.get("/top")
def get_top_edges(
    limit: int = Query(default=10, le=100),
    include_all_dates: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    today = datetime.now(ET).date()
    rows = get_trustworthy_active_edges(
        db,
        game_date=None if include_all_dates else today,
    )

    latest_by_game = {}
    for edge, _game, _prediction, _odds in rows:
        if edge.game_id not in latest_by_game:
            latest_by_game[edge.game_id] = edge

    top_rows = sorted(
        latest_by_game.values(),
        key=lambda edge: float(edge.edge_pct or 0),
        reverse=True,
    )[:limit]

    return [
        {
            "game_id": r.game_id,
            "play": r.recommended_play,
            "edge_pct": float(r.edge_pct) if r.edge_pct is not None else None,
            "ev_away": float(r.ev_away) if r.ev_away is not None else None,
            "ev_home": float(r.ev_home) if r.ev_home is not None else None,
            "confidence": r.confidence_tier,
            "pitching_edge_score": float(r.pitching_edge_score) if getattr(r, "pitching_edge_score", None) is not None else None,
            "calculated_at": r.calculated_at.isoformat() if r.calculated_at else None,
        }
        for r in top_rows
    ]


@router.get("/history/top")
def get_top_edges_history(
    limit: int = Query(default=10, le=100),
    db: Session = Depends(get_db),
):
    return get_top_edges(limit=limit, include_all_dates=True, db=db)


@router.get("/today")
def get_today_edges(db: Session = Depends(get_db)):
    today = datetime.now(ET).date()
    trusted_rows = get_trustworthy_active_edges(db, game_date=today)
    latest_by_game = {}
    for edge, _game, _prediction, _odds in trusted_rows:
        if edge.game_id not in latest_by_game:
            latest_by_game[edge.game_id] = edge
    rows = list(latest_by_game.values())

    return [
        {
            "game_id": r.game_id,
            "play": r.recommended_play,
            "edge_pct": float(r.edge_pct) if r.edge_pct is not None else None,
            "ev_away": float(r.ev_away) if r.ev_away is not None else None,
            "ev_home": float(r.ev_home) if r.ev_home is not None else None,
            "ev_over": float(r.ev_over) if r.ev_over is not None else None,
            "ev_under": float(r.ev_under) if r.ev_under is not None else None,
            "confidence": r.confidence_tier,
            "movement_direction": r.movement_direction,
            "model_away_win_pct": float(r.model_away_win_pct) if r.model_away_win_pct is not None else None,
            "model_home_win_pct": float(r.model_home_win_pct) if r.model_home_win_pct is not None else None,
            "model_total": float(r.model_total) if r.model_total is not None else None,
            "book_total": float(r.book_total) if r.book_total is not None else None,
            "calculated_at": r.calculated_at.isoformat() if r.calculated_at else None,
        }
        for r in rows
    ]
