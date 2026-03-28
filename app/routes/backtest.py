import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.models.schema import BacktestResult
from app.services.backtest_service import collect_season, run_logistic_regression

router = APIRouter(prefix="/api/backtest", tags=["backtest"])
logger = logging.getLogger(__name__)


@router.post("/collect")
def collect_backtest_data(
    background_tasks: BackgroundTasks,
    seasons: str = Query("2022,2023,2024"),
):
    """
    Fetch historical game data from the MLB Stats API and store in backtest_games.
    Runs as a background task — can take several minutes per season. Watch server logs:
      sudo journalctl -u mlb-betting -f
    """
    season_list = [int(x.strip()) for x in seasons.split(",") if x.strip()]

    def _collect():
        # Open a fresh session per season so a long-running collection doesn't
        # exhaust Neon's idle connection timeout across multiple seasons.
        total = 0
        for s in season_list:
            _db = SessionLocal()
            try:
                logger.info("[backtest] Starting season %s collection", s)
                n = collect_season(_db, s)
                total += n
                logger.info("[backtest] Season %s done: %d games stored", s, n)
            except Exception as exc:
                logger.error("[backtest] Season %s failed: %s: %s",
                             s, type(exc).__name__, exc)
            finally:
                _db.close()

        logger.info("[backtest] All seasons complete: %d total games for %s",
                    total, season_list)

    background_tasks.add_task(_collect)
    return {
        "status": "collecting",
        "seasons": season_list,
        "message": "Background collection started. Follow progress with: sudo journalctl -u mlb-betting -f",
    }


@router.post("/run")
def run_backtest_route(
    seasons: str = Query("2022,2023,2024"),
    db: Session = Depends(get_db),
):
    """
    Train logistic regression on backtest_games and update simulator weights.
    Requires /collect to have been run first.
    """
    season_list = [int(x.strip()) for x in seasons.split(",") if x.strip()]
    result = run_logistic_regression(db, season_list)
    return {
        "status": "model trained",
        "seasons": result.seasons,
        "n_games": result.n_games,
        "accuracy": result.accuracy,
        "cv_accuracy": result.cv_accuracy,
        "log_loss": result.log_loss,
        "feature_ranks": json.loads(result.feature_ranks_json),
        "coefficients": json.loads(result.coefficients_json),
    }


@router.get("/latest")
def get_latest_backtest(db: Session = Depends(get_db)):
    """Return the most recent backtest result, or null if none exists."""
    result = (
        db.query(BacktestResult)
        .order_by(BacktestResult.run_at.desc())
        .first()
    )
    if not result:
        return None
    return {
        "run_at": result.run_at.isoformat() if result.run_at else None,
        "seasons": result.seasons,
        "n_games": result.n_games,
        "accuracy": result.accuracy,
        "cv_accuracy": result.cv_accuracy,
        "log_loss": result.log_loss,
        "feature_ranks": json.loads(result.feature_ranks_json),
    }
