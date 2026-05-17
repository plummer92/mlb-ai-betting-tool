from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.middleware.auth import verify_api_key
from app.services.market_audit_service import get_clv_report
from app.services.paper_trade_service import backfill_missing_paper_trades, get_paper_summary

router = APIRouter(prefix="/api/paper", tags=["paper"])


@router.get("/summary")
def paper_summary(db: Session = Depends(get_db)):
    return get_paper_summary(db)


@router.get("/clv")
def paper_clv(db: Session = Depends(get_db)):
    return get_clv_report(db)


@router.post("/backfill", dependencies=[Depends(verify_api_key)])
def paper_backfill(
    limit: int | None = Query(None, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    return backfill_missing_paper_trades(db, limit=limit)
