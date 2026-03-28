from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import EdgeResult, Game

ET = ZoneInfo("America/New_York")

router = APIRouter(prefix="/api/edges", tags=["edges"])


@router.get("/top")
def get_top_edges(
    limit: int = Query(default=10, le=100),
    db: Session = Depends(get_db),
):
    # Latest edge row per game only
    subq = (
        db.query(
            EdgeResult.game_id,
            func.max(EdgeResult.calculated_at).label("latest_time"),
        )
        .group_by(EdgeResult.game_id)
        .subquery()
    )

    rows = (
        db.query(EdgeResult)
        .join(
            subq,
            (EdgeResult.game_id == subq.c.game_id)
            & (EdgeResult.calculated_at == subq.c.latest_time),
        )
        .order_by(EdgeResult.edge_pct.desc())
        .limit(limit)
        .all()
    )

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
        for r in rows
    ]


@router.get("/today")
def get_today_edges(db: Session = Depends(get_db)):
    today = datetime.now(ET).date()

    subq = (
        db.query(
            EdgeResult.game_id,
            func.max(EdgeResult.calculated_at).label("latest_time"),
        )
        .join(Game, EdgeResult.game_id == Game.game_id)
        .filter(Game.game_date == today)
        .group_by(EdgeResult.game_id)
        .subquery()
    )

    rows = (
        db.query(EdgeResult)
        .join(
            subq,
            (EdgeResult.game_id == subq.c.game_id)
            & (EdgeResult.calculated_at == subq.c.latest_time),
        )
        .all()
    )

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
