from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import BacktestResult, GameOutcomeReview, Prediction
from app.scheduler import scheduler
from app.services.simulator import MODEL_VERSION

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
def get_system_status(db: Session = Depends(get_db)):
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
        })

    total_reviews = db.query(func.count(GameOutcomeReview.id)).scalar() or 0
    wins = db.query(func.count(GameOutcomeReview.id)).filter(GameOutcomeReview.bet_result == "win").scalar() or 0
    losses = db.query(func.count(GameOutcomeReview.id)).filter(GameOutcomeReview.bet_result == "loss").scalar() or 0
    pushes = db.query(func.count(GameOutcomeReview.id)).filter(GameOutcomeReview.bet_result == "push").scalar() or 0
    correct = (
        db.query(func.count(GameOutcomeReview.id))
        .filter(GameOutcomeReview.was_model_correct == True)  # noqa: E712
        .scalar() or 0
    )
    bets_graded = wins + losses + pushes
    total_preds = db.query(func.count(Prediction.prediction_id)).scalar() or 0

    backtest = db.query(BacktestResult).order_by(BacktestResult.run_at.desc()).first()

    return {
        "jobs": jobs,
        "model": {
            "version": MODEL_VERSION,
            "total_predictions": total_preds,
            "winner_accuracy_pct": round(correct / total_reviews * 100, 1) if total_reviews > 0 else None,
            "bets_graded": bets_graded,
            "bet_win_rate": round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else None,
            "total_graded": total_reviews,
        },
        "backtest": {
            "accuracy": backtest.accuracy,
            "cv_accuracy": backtest.cv_accuracy,
            "n_games": backtest.n_games,
            "seasons": backtest.seasons,
            "run_at": backtest.run_at.isoformat() if backtest.run_at else None,
        } if backtest else None,
    }
