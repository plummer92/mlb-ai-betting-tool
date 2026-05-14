from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.paper_trade_service import get_paper_summary

router = APIRouter(prefix="/api/paper", tags=["paper"])


@router.get("/summary")
def paper_summary(db: Session = Depends(get_db)):
    return get_paper_summary(db)
