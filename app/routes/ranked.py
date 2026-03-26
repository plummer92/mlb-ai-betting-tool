from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime
from zoneinfo import ZoneInfo

from app.db import get_db
from app.models.schema import BetAlert

router = APIRouter(prefix="/api", tags=["ranked"])
ET = ZoneInfo("America/New_York")


def compute_rank_score(edge_pct: float, ev: float, confidence: str) -> float:
    confidence_bonus = 10 if confidence == "strong" else 5 if confidence == "medium" else 0
    return (ev * 100) + (edge_pct * 75) + confidence_bonus


@router.get("/alerts/ranked")
def ranked_alerts_today(db: Session = Depends(get_db)):
    today = datetime.now(ET).date()
    rows = (
        db.query(BetAlert)
        .filter(BetAlert.game_date == today)
        .all()
    )

    ranked = []
    for row in rows:
        score = compute_rank_score(
            float(row.edge_pct),
            float(row.ev),
            row.confidence,
        )
        ranked.append({
            "game_id": row.game_id,
            "play": row.play,
            "edge_pct": float(row.edge_pct),
            "ev": float(row.ev),
            "confidence": row.confidence,
            "synopsis": row.synopsis,
            "rank_score": round(score, 2),
        })

    ranked.sort(key=lambda x: x["rank_score"], reverse=True)

    for i, row in enumerate(ranked, start=1):
        row["rank"] = i

    return ranked
