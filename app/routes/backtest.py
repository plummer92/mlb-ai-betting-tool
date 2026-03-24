"""
Backtest endpoints.

POST /api/backtest/collect?season=2022   — collect one season (~5-10 min)
POST /api/backtest/run?seasons=2022,2023,2024  — run logistic regression
GET  /api/backtest/results               — latest regression output
GET  /api/backtest/status                — games collected per season
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import BacktestGame, BacktestResult
from app.services.backtest_collector import collect_season
from app.services.backtest_runner import run_regression

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

VALID_SEASONS = {2022, 2023, 2024}


@router.post("/collect")
def collect(
    season: int = Query(..., description="Season to collect: 2022, 2023, or 2024"),
    db: Session = Depends(get_db),
):
    """
    Fetch and store all completed regular-season games for one season.
    Takes 5-10 minutes per season due to individual pitcher stat calls.
    Idempotent — already-stored games are skipped.
    """
    if season not in VALID_SEASONS:
        raise HTTPException(status_code=400, detail=f"season must be one of {sorted(VALID_SEASONS)}")

    result = collect_season(season, db)
    return result


@router.post("/run")
def run_backtest(
    seasons: str = Query("2022,2023,2024", description="Comma-separated seasons to include"),
    db: Session = Depends(get_db),
):
    """
    Run logistic regression on collected historical games.
    Returns feature coefficients ranked by importance.
    Requires at least one season of data to be collected first.
    """
    try:
        season_list = [int(s.strip()) for s in seasons.split(",")]
    except ValueError:
        raise HTTPException(status_code=400, detail="seasons must be comma-separated integers")

    invalid = [s for s in season_list if s not in VALID_SEASONS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid seasons: {invalid}")

    n_available = db.query(func.count(BacktestGame.game_id)).filter(
        BacktestGame.season.in_(season_list)
    ).scalar()

    if n_available < 100:
        raise HTTPException(
            status_code=400,
            detail=f"Only {n_available} games collected for {season_list}. "
                   f"Run POST /api/backtest/collect?season=YYYY first."
        )

    result = run_regression(db, season_list)
    return result


@router.get("/results")
def get_results(db: Session = Depends(get_db)):
    """Return the most recent regression result."""
    latest = (
        db.query(BacktestResult)
        .order_by(BacktestResult.run_at.desc())
        .first()
    )
    if not latest:
        raise HTTPException(status_code=404, detail="No backtest results yet. Run POST /api/backtest/run first.")

    return {
        "result_id":   latest.id,
        "run_at":      latest.run_at.isoformat(),
        "seasons":     latest.seasons,
        "n_games":     latest.n_games,
        "test_accuracy":  latest.accuracy,
        "cv_accuracy":    latest.cv_accuracy,
        "log_loss":       latest.log_loss,
        "coefficients":   json.loads(latest.coefficients_json),
        "feature_ranks":  json.loads(latest.feature_ranks_json),
    }


@router.get("/status")
def get_status(db: Session = Depends(get_db)):
    """Show how many games have been collected per season."""
    rows = (
        db.query(BacktestGame.season, func.count(BacktestGame.game_id))
        .group_by(BacktestGame.season)
        .order_by(BacktestGame.season)
        .all()
    )
    collected = {str(season): count for season, count in rows}
    total = sum(collected.values()) if collected else 0

    return {
        "collected_by_season": collected,
        "total_games": total,
        "ready_to_run": total >= 100,
        "note": "Full 3-season backtest needs ~7300 games. Each season ~2430 games, ~5-10 min to collect.",
    }
