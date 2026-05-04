"""
Bullpen fatigue and manager tendency API routes.
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import Game
from app.services.manager_service import get_bullpen_fatigue_report

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bullpen", tags=["bullpen"])


@router.get("/today")
def bullpen_today(db: Session = Depends(get_db)):
    """
    Bullpen fatigue report for all teams playing today.
    Sorted by bullpen_strength ascending (most fatigued first).
    """
    today = date.today()
    games = db.query(Game).filter(Game.game_date == today).all()

    team_ids: set[int] = set()
    for g in games:
        if g.home_team_id:
            team_ids.add(g.home_team_id)
        if g.away_team_id:
            team_ids.add(g.away_team_id)

    reports = []
    for team_id in team_ids:
        try:
            reports.append(get_bullpen_fatigue_report(team_id, db))
        except Exception:
            logger.exception("[bullpen] report error team=%d", team_id)

    reports.sort(key=lambda r: r.get("bullpen_strength", 1.0))
    return reports
