from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.admin_service import backfill_prediction_dashboard_metrics, get_pipeline_freshness

router = APIRouter(prefix="/api/admin", tags=["admin"])
ET = ZoneInfo("America/New_York")


def _parse_target_date(value) -> datetime.date | None:
    if value is None:
        return None
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return value
    return None


@router.get("/freshness")
def admin_freshness(
    target_date: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    parsed_date = _parse_target_date(target_date)
    return get_pipeline_freshness(db, target_date=parsed_date)


@router.post("/backfill/prediction-dashboard-metrics")
def admin_backfill_prediction_dashboard_metrics(
    target_date: str | None = Query(default=None),
    active_only: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    parsed_date = _parse_target_date(target_date)
    return backfill_prediction_dashboard_metrics(
        db,
        target_date=parsed_date or datetime.now(ET).date(),
        active_only=active_only,
    )
