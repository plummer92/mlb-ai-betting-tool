from fastapi import APIRouter, Query
from app.backtest.run_backtest import run_backtest

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.post("/run")
def run_backtest_route(seasons: str = Query("2022,2023,2024")):
    season_list = [int(x.strip()) for x in seasons.split(",") if x.strip()]
    return run_backtest(season_list)
