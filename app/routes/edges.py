from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import EdgeResult
from app.models.pydantic_models import EdgeResultOut

router = APIRouter(prefix="/api/edges", tags=["edges"])


@router.get("/today", response_model=list[EdgeResultOut])
def get_today_edges(db: Session = Depends(get_db)):
    et = ZoneInfo("America/New_York")
    today_start = datetime.now(et).replace(hour=0, minute=0, second=0, microsecond=0)
    results = (
        db.query(EdgeResult)
        .filter(EdgeResult.calculated_at >= today_start)
        .order_by(EdgeResult.edge_pct.desc())
        .all()
    )
    return results


@router.get("/top")
def get_top_edges(db: Session = Depends(get_db)):
    et = ZoneInfo("America/New_York")
    today_start = datetime.now(et).replace(hour=0, minute=0, second=0, microsecond=0)
    results = (
        db.query(EdgeResult)
        .filter(EdgeResult.calculated_at >= today_start)
        .order_by(EdgeResult.edge_pct.desc())
        .limit(10)
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
            "calculated_at": r.calculated_at.isoformat() if r.calculated_at else None,
        }
        for r in results
    ]
