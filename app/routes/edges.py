from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import Game
from app.services.edge_service import get_trustworthy_active_edges

ET = ZoneInfo("America/New_York")

router = APIRouter(prefix="/api/edges", tags=["edges"])


@router.get("/top")
def get_top_edges(
    limit: int = Query(default=10, le=100),
    include_all_dates: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    today = datetime.now(ET).date()
    rows = get_trustworthy_active_edges(
        db,
        game_date=None if include_all_dates else today,
    )

    latest_by_game = {}
    for edge, _game, _prediction, _odds in rows:
        if edge.game_id not in latest_by_game:
            latest_by_game[edge.game_id] = edge

    top_rows = sorted(
        latest_by_game.values(),
        key=lambda edge: float(edge.edge_pct or 0),
        reverse=True,
    )[:limit]

    return [
        {
            "game_id": r.game_id,
            "play": r.recommended_play,
            "edge_pct": float(r.edge_pct) if r.edge_pct is not None else None,
            "ev_away": float(r.ev_away) if r.ev_away is not None else None,
            "ev_home": float(r.ev_home) if r.ev_home is not None else None,
            "confidence": r.confidence_tier,
            "pitching_edge_score": float(r.pitching_edge_score) if getattr(r, "pitching_edge_score", None) is not None else None,
            "calculated_at": r.calculated_at.isoformat() if r.calculated_at else None,
        }
        for r in top_rows
    ]


@router.get("/history/top")
def get_top_edges_history(
    limit: int = Query(default=10, le=100),
    db: Session = Depends(get_db),
):
    return get_top_edges(limit=limit, include_all_dates=True, db=db)


@router.get("/today")
def get_today_edges(db: Session = Depends(get_db)):
    today = datetime.now(ET).date()
    trusted_rows = get_trustworthy_active_edges(db, game_date=today)
    latest_by_game: dict[int, dict] = {}
    for edge, game, prediction, odds in trusted_rows:
        if edge.game_id in latest_by_game:
            continue
        latest_by_game[edge.game_id] = {
            "game_id": game.game_id,
            "play": edge.recommended_play,
            "edge_pct": float(edge.edge_pct) if edge.edge_pct is not None else None,
            "ev_away": float(edge.ev_away) if edge.ev_away is not None else None,
            "ev_home": float(edge.ev_home) if edge.ev_home is not None else None,
            "ev_over": float(edge.ev_over) if edge.ev_over is not None else None,
            "ev_under": float(edge.ev_under) if edge.ev_under is not None else None,
            "confidence": edge.confidence_tier,
            "movement_direction": edge.movement_direction,
            "model_away_win_pct": float(edge.model_away_win_pct) if edge.model_away_win_pct is not None else None,
            "model_home_win_pct": float(edge.model_home_win_pct) if edge.model_home_win_pct is not None else None,
            "implied_away_pct": float(edge.implied_away_pct) if edge.implied_away_pct is not None else None,
            "implied_home_pct": float(edge.implied_home_pct) if edge.implied_home_pct is not None else None,
            "model_total": float(edge.model_total) if edge.model_total is not None else None,
            "book_total": float(edge.book_total) if edge.book_total is not None else None,
            "calculated_at": edge.calculated_at.isoformat() if edge.calculated_at else None,
            "sportsbook": edge.sportsbook or (odds.sportsbook if odds else None),
            "snapshot_type": edge.odds_snapshot_type or (odds.snapshot_type.value if odds and odds.snapshot_type else None),
            "away_ml": edge.away_ml if edge.away_ml is not None else (odds.away_ml if odds else None),
            "home_ml": edge.home_ml if edge.home_ml is not None else (odds.home_ml if odds else None),
            "over_odds": edge.over_odds if edge.over_odds is not None else (odds.over_odds if odds else None),
            "under_odds": edge.under_odds if edge.under_odds is not None else (odds.under_odds if odds else None),
            "kbb_adv": prediction.kbb_adv,
            "pythagorean_win_pct_adv": prediction.pythagorean_win_pct_adv,
            "park_factor_adv": prediction.park_factor_adv,
        }

    return list(latest_by_game.values())
