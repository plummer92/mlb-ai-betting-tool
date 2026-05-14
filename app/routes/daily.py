from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.middleware.auth import verify_api_key
from app.middleware.limiter import limiter
from app.services.pipeline_service import run_daily_pipeline, run_pregame_pipeline

router = APIRouter(prefix="/api", tags=["daily"])


@router.post("/daily-run", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def daily_run(request: Request, db: Session = Depends(get_db)):
    return await run_daily_pipeline(db)


@router.post("/pregame-run", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def pregame_run(request: Request, db: Session = Depends(get_db)):
    return await run_pregame_pipeline(db)
