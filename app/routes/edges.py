from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import Game
from app.services.edge_service import get_trustworthy_active_edges
from app.services.market_respect_service import market_respect_adjustment, market_respect_for_edge

ET = ZoneInfo("America/New_York")

router = APIRouter(prefix="/api/edges", tags=["edges"])


def _pick_ev(edge) -> float:
    play = (edge.recommended_play or "").lower()
    if play == "away_ml":
        return float(edge.ev_away or 0)
    if play == "home_ml":
        return float(edge.ev_home or 0)
    if play == "over":
        return float(edge.ev_over or 0)
    if play == "under":
        return float(edge.ev_under or 0)
    return 0.0


def _pick_odds(edge, odds) -> int | None:
    play = (edge.recommended_play or "").lower()
    if play == "away_ml":
        return edge.away_ml if edge.away_ml is not None else (odds.away_ml if odds else None)
    if play == "home_ml":
        return edge.home_ml if edge.home_ml is not None else (odds.home_ml if odds else None)
    if play == "over":
        return edge.over_odds if edge.over_odds is not None else (odds.over_odds if odds else None)
    if play == "under":
        return edge.under_odds if edge.under_odds is not None else (odds.under_odds if odds else None)
    return None


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
    for edge, game, _prediction, odds in rows:
        if edge.game_id not in latest_by_game:
            latest_by_game[edge.game_id] = (edge, game, odds)

    top_rows = sorted(
        latest_by_game.values(),
        key=lambda row: float(row[0].edge_pct or 0),
        reverse=True,
    )[:limit]

    output = []
    for edge, game, odds in top_rows:
        market_respect = market_respect_for_edge(db, edge, odds=odds, game=game)
        adjustment = market_respect_adjustment(
            edge_pct=float(edge.edge_pct or 0),
            ev=_pick_ev(edge),
            confidence=edge.confidence_tier,
            market_respect=market_respect,
            odds_american=_pick_odds(edge, odds),
        )
        output.append({
            "game_id": edge.game_id,
            "play": edge.recommended_play,
            "edge_pct": float(edge.edge_pct) if edge.edge_pct is not None else None,
            "raw_edge_pct": adjustment["raw_edge_pct"],
            "adjusted_edge_pct": adjustment["adjusted_edge_pct"],
            "adjusted_ev": adjustment["adjusted_ev"],
            "adjusted_confidence": adjustment["adjusted_confidence"],
            "adjusted_kelly_fraction": adjustment["adjusted_kelly_fraction"],
            "ev_away": float(edge.ev_away) if edge.ev_away is not None else None,
            "ev_home": float(edge.ev_home) if edge.ev_home is not None else None,
            "confidence": edge.confidence_tier,
            "pitching_edge_score": float(edge.pitching_edge_score) if getattr(edge, "pitching_edge_score", None) is not None else None,
            "market_respect": market_respect,
            "market_respect_adjustment": adjustment,
            "calculated_at": edge.calculated_at.isoformat() if edge.calculated_at else None,
        })
    return output


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
        market_respect = market_respect_for_edge(db, edge, odds=odds, game=game)
        adjustment = market_respect_adjustment(
            edge_pct=float(edge.edge_pct or 0),
            ev=_pick_ev(edge),
            confidence=edge.confidence_tier,
            market_respect=market_respect,
            odds_american=_pick_odds(edge, odds),
        )
        latest_by_game[edge.game_id] = {
            "game_id": game.game_id,
            "play": edge.recommended_play,
            "edge_pct": float(edge.edge_pct) if edge.edge_pct is not None else None,
            "raw_edge_pct": adjustment["raw_edge_pct"],
            "adjusted_edge_pct": adjustment["adjusted_edge_pct"],
            "raw_ev": adjustment["raw_ev"],
            "adjusted_ev": adjustment["adjusted_ev"],
            "adjusted_confidence": adjustment["adjusted_confidence"],
            "adjusted_kelly_fraction": adjustment["adjusted_kelly_fraction"],
            "ev_away": float(edge.ev_away) if edge.ev_away is not None else None,
            "ev_home": float(edge.ev_home) if edge.ev_home is not None else None,
            "ev_over": float(edge.ev_over) if edge.ev_over is not None else None,
            "ev_under": float(edge.ev_under) if edge.ev_under is not None else None,
            "confidence": edge.confidence_tier,
            "movement_direction": edge.movement_direction,
            "market_respect": market_respect,
            "market_respect_score": market_respect["score"],
            "market_respect_tags": market_respect["tags"],
            "market_trust_bucket": adjustment["bucket"],
            "market_respect_adjustment": adjustment,
            "market_respect_alert_allowed": adjustment["alert_allowed"],
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
