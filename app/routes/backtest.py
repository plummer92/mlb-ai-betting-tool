import json
import traceback

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.models.schema import BacktestResult
from app.services.backtest_service import POINT_IN_TIME_WARNING, collect_season, run_analysis, run_logistic_regression

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


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
        try:
            # Open a fresh session per season so a long-running collection doesn't
            # exhaust Neon's idle connection timeout across multiple seasons.
            total = 0
            for s in season_list:
                print(f"[backtest] Opening DB session for season {s}", flush=True)
                _db = SessionLocal()
                try:
                    n = collect_season(_db, s)
                    total += n
                    print(f"[backtest] Season {s} done: {n} games stored", flush=True)
                except Exception:
                    print(f"[backtest] Season {s} FAILED:", flush=True)
                    traceback.print_exc()
                finally:
                    _db.close()
                    print(f"[backtest] DB session for season {s} closed", flush=True)

            print(f"[backtest] All seasons complete: {total} total games for {season_list}", flush=True)
        except Exception:
            print("[backtest] Unexpected error in background _collect():", flush=True)
            traceback.print_exc()

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
        "warning": POINT_IN_TIME_WARNING,
        "seasons": result.seasons,
        "n_games": result.n_games,
        "accuracy": result.accuracy,
        "cv_accuracy": result.cv_accuracy,
        "log_loss": result.log_loss,
        "brier_score": result.brier_score,
        "calibration_params": json.loads(result.calibration_params_json) if result.calibration_params_json else None,
        "feature_ranks": json.loads(result.feature_ranks_json),
        "coefficients": json.loads(result.coefficients_json),
        "dataset_summary": json.loads(result.dataset_summary_json) if result.dataset_summary_json else None,
        "validation_summary": json.loads(result.validation_summary_json) if result.validation_summary_json else None,
        "limitations": json.loads(result.limitations_json) if result.limitations_json else None,
    }


@router.get("/analysis")
def backtest_analysis_report(
    seasons: str = Query("2022,2023,2024"),
    db: Session = Depends(get_db),
):
    """
    Correlation analysis on historical backtest_games data.
    Returns:
    - Pearson correlations for all features vs home_win
    - Run-differential quintile lifts
    - Venue / park-factor breakdown
    - Season-by-season home win rates
    - Prioritised recommendations for model improvements
    """
    season_list = [int(x.strip()) for x in seasons.split(",") if x.strip()]
    return run_analysis(db, season_list)


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
        "brier_score": result.brier_score,
        "calibrated": result.calibration_params_json is not None,
        "feature_ranks": json.loads(result.feature_ranks_json),
        "dataset_summary": json.loads(result.dataset_summary_json) if result.dataset_summary_json else None,
        "validation_summary": json.loads(result.validation_summary_json) if result.validation_summary_json else None,
        "limitations": json.loads(result.limitations_json) if result.limitations_json else None,
    }
