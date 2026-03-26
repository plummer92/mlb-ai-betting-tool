import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.models.schema import BacktestResult
from app.services.backtest_service import collect_season, run_logistic_regression

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

DEFAULT_SEASONS = [2022, 2023, 2024]


@router.post("/collect")
def collect_backtest_data(
    seasons: str = "2022,2023,2024",
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """
    Fetch historical game results and team stats for the given seasons.
    Runs as a background task — returns immediately.
    seasons: comma-separated list, e.g. "2022,2023,2024"
    """
    season_list = [int(s.strip()) for s in seasons.split(",")]

    def _run():
        db = SessionLocal()
        try:
            for s in season_list:
                collect_season(db, s)
        finally:
            db.close()

    background_tasks.add_task(_run)
    return {"message": "Collection started", "seasons": season_list}


@router.post("/run")
def run_backtest(
    seasons: str = "2022,2023,2024",
    db: Session = Depends(get_db),
):
    """
    Run logistic regression on collected backtest data and return feature report.
    Call /collect first if backtest_games is empty.
    """
    season_list = [int(s.strip()) for s in seasons.split(",")]
    try:
        result = run_logistic_regression(db, season_list)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return _format_result(result)


@router.get("/report")
def get_latest_report(db: Session = Depends(get_db)):
    """Return the most recent backtest regression report."""
    result = db.query(BacktestResult).order_by(BacktestResult.run_at.desc()).first()
    if not result:
        raise HTTPException(status_code=404, detail="No backtest results found. Run /api/backtest/run first.")
    return _format_result(result)


def _format_result(r: BacktestResult) -> dict:
    return {
        "id": r.id,
        "run_at": r.run_at.isoformat() if r.run_at else None,
        "seasons": r.seasons,
        "n_games": r.n_games,
        "accuracy": r.accuracy,
        "cv_accuracy": r.cv_accuracy,
        "log_loss": r.log_loss,
        "feature_importance": json.loads(r.feature_ranks_json),
        "coefficients": json.loads(r.coefficients_json),
    }
