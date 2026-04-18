from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.pipeline_service import run_daily_pipeline, run_pregame_pipeline

router = APIRouter(prefix="/api", tags=["daily"])


@router.post("/daily-run")
async def daily_run(db: Session = Depends(get_db)):
    return await run_daily_pipeline(db)


@router.post("/pregame-run")
async def pregame_run(db: Session = Depends(get_db)):
    return await run_pregame_pipeline(db)
