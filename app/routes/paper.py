from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.middleware.auth import verify_api_key
from app.services.market_audit_service import get_clv_report
from app.services.paper_trade_service import backfill_missing_paper_trades, get_paper_summary
from app.services.report_cache import get_ttl_cached
from app.services.report_snapshot_service import get_report_snapshot

router = APIRouter(prefix="/api/paper", tags=["paper"])


@router.get("/summary")
def paper_summary(db: Session = Depends(get_db)):
    return get_paper_summary(db)


@router.get("/clv")
def paper_clv(
    limit: int = Query(default=25, ge=25, le=1000),
    db: Session = Depends(get_db),
):
    if limit == 25:
        snapshot = get_report_snapshot(db, "paper_clv", report_date=date.today())
        if snapshot is not None:
            return snapshot
    return get_ttl_cached(
        ("paper_clv", limit),
        ttl_seconds=300,
        builder=lambda: get_clv_report(db, limit=limit),
    )


@router.post("/backfill", dependencies=[Depends(verify_api_key)])
def paper_backfill(
    limit: int | None = Query(None, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    return backfill_missing_paper_trades(db, limit=limit)
