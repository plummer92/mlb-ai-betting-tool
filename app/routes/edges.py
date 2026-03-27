from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import EdgeResult

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
