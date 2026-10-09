from datetime import date, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.middleware.auth import verify_api_key
from app.middleware.limiter import limiter
from app.services.daily_run_guard import DailyRunGuard
from app.services.pipeline_service import ET, run_daily_pipeline, run_pregame_pipeline

router = APIRouter(prefix="/api", tags=["daily"])


@router.post("/daily-run", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def daily_run(request: Request, target_date: date | None = None, db: Session = Depends(get_db)):
    return _daily_response(await run_daily_pipeline(db, target_date))


def _daily_response(result: dict):
    status = result.get("status")
    if status == "running":
        return JSONResponse(result, status_code=202, headers={"Retry-After": "30"})
    if status in {"failed", "blocked"}:
        return JSONResponse(result, status_code=500)
    if status == "not_found":
        return JSONResponse(result, status_code=404)
    return result


@router.get("/daily-run/status", dependencies=[Depends(verify_api_key)])
async def daily_run_status(target_date: date | None = None, db: Session = Depends(get_db)):
    today = target_date or datetime.now(ET).date()
    return _daily_response(DailyRunGuard(db, today).status())


@router.post("/pregame-run", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def pregame_run(request: Request, db: Session = Depends(get_db)):
    return await run_pregame_pipeline(db)
