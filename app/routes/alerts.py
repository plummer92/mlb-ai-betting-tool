from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import BetAlert, GameOutcomeReview, Prediction

CURRENT_MODEL = "v0.2-backtest-weighted"
from app.services.alert_service import create_and_send_alerts_for_today
from app.services.review_service import get_accuracy_segmented, resolve_completed_games

router = APIRouter(prefix="/api", tags=["alerts"])
ET = ZoneInfo("America/New_York")


@router.post("/alerts/run")
def run_alerts(db: Session = Depends(get_db)):
    return create_and_send_alerts_for_today(db)


@router.post("/alerts/send")
def send_alerts(db: Session = Depends(get_db)):
    result = create_and_send_alerts_for_today(db)
    return {
        "sent": result.get("sent", 0),
        "skipped": result.get("skipped", 0),
        "failed": result.get("failed", 0),
    }


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
            "total_correct": r.total_correct,
            "projected_away_score": float(r.projected_away_score) if r.projected_away_score is not None else None,
            "projected_home_score": float(r.projected_home_score) if r.projected_home_score is not None else None,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/reviews/accuracy")
def reviews_accuracy(db: Session = Depends(get_db)):
    # Calculate segmented accuracy using the service
    segmented = get_accuracy_segmented(db, CURRENT_MODEL)
    
    # We still want to include last_10 and current model info for completeness
    last_10_rows = (
        db.query(GameOutcomeReview)
        .order_by(GameOutcomeReview.created_at.desc())
        .limit(10)
        .all()
    )
    
    last_10 = [
        {
            "game_id": r.game_id,
            "game_date": str(r.game_date),
            "predicted_winner": (
                "away" if (r.model_away_win_pct or 0) >= (r.model_home_win_pct or 0) else "home"
            ),
            "actual_winner": r.winning_side,
            "was_correct": r.was_model_correct,
            "projected_away": float(r.projected_away_score) if r.projected_away_score is not None else None,
            "projected_home": float(r.projected_home_score) if r.projected_home_score is not None else None,
            "actual_away": r.final_away_score,
            "actual_home": r.final_home_score,
            "model_total": float(r.model_total) if r.model_total is not None else None,
            "actual_total": (r.final_away_score or 0) + (r.final_home_score or 0),
            "total_correct": r.total_correct,
            "recommended_play": r.recommended_play,
            "bet_result": r.bet_result,
        }
        for r in last_10_rows
    ]

    return {
        "overall": segmented["overall"],
        "moneyline": segmented["moneyline"],
        "totals": segmented["totals"],
        "run_line": segmented["run_line"],
        "current_model": CURRENT_MODEL,
        "last_10": last_10,
    }
