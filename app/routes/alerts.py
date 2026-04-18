from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import BetAlert

from app.services.alert_service import create_and_send_alerts_for_today

router = APIRouter(prefix="/api/alerts", tags=["alerts"])
ET = ZoneInfo("America/New_York")


@router.post("/run")
def run_alerts(db: Session = Depends(get_db)):
    return create_and_send_alerts_for_today(db)


@router.post("/send")
def send_alerts(db: Session = Depends(get_db)):
    result = create_and_send_alerts_for_today(db)
    return {
        "sent": result.get("sent", 0),
        "skipped": result.get("skipped", 0),
        "failed": result.get("failed", 0),
    }


@router.get("/today")
def alerts_today(db: Session = Depends(get_db)):
    today = datetime.now(ET).date()
    rows = db.query(BetAlert).filter(BetAlert.game_date == today).order_by(BetAlert.alert_time.desc()).all()
    return [
        {
            "id": r.id,
            "game_id": r.game_id,
            "play": r.play,
            "edge_pct": float(r.edge_pct),
            "ev": float(r.ev),
            "confidence": r.confidence,
            "status": r.status,
            "synopsis": r.synopsis,
            "bet_result": r.bet_result,
            "alert_time": r.alert_time,
        }
        for r in rows
    ]

