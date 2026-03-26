from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import BetAlert, GameOutcomeReview
from app.services.alert_service import create_and_send_alerts_for_today
from app.services.review_service import resolve_completed_games

router = APIRouter(prefix="/api", tags=["alerts"])
ET = ZoneInfo("America/New_York")


@router.post("/alerts/run")
def run_alerts(db: Session = Depends(get_db)):
    return create_and_send_alerts_for_today(db)


@router.get("/alerts/today")
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


@router.post("/reviews/resolve")
def resolve_reviews(db: Session = Depends(get_db)):
    return resolve_completed_games(db)


@router.get("/reviews/recent")
def recent_reviews(limit: int = 25, db: Session = Depends(get_db)):
    rows = db.query(GameOutcomeReview).order_by(GameOutcomeReview.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "game_id": r.game_id,
            "recommended_play": r.recommended_play,
            "bet_result": r.bet_result,
            "final_away_score": r.final_away_score,
            "final_home_score": r.final_home_score,
            "pre_game_synopsis": r.pre_game_synopsis,
            "actual_outcome_summary": r.actual_outcome_summary,
            "was_model_correct": r.was_model_correct,
            "created_at": r.created_at,
        }
        for r in rows
    ]
