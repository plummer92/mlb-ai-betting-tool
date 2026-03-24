from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import EdgeResult, Game, LineMovement
from app.services.edge_service import calculate_all_edges_today, calculate_edge_for_game
from app.services.odds_service import fetch_and_store_odds
from app.models.schema import SnapshotType

router = APIRouter(prefix="/api/edges", tags=["edges"])


@router.post("/sync-odds")
async def sync_odds(
    snapshot_type: str = "open",
    db: Session = Depends(get_db),
):
    """
    Pull latest moneylines + totals from sportsbooks.
    snapshot_type: "open" (morning) or "pregame" (45 min before first pitch).
    Typical use: cron every game day morning for open, then per-game pre-game.
    """
    try:
        stype = SnapshotType(snapshot_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid snapshot_type: {snapshot_type}")

    stored = await fetch_and_store_odds(db, snapshot_type=stype)
    return {"stored": len(stored), "snapshot_type": snapshot_type, "message": "Odds snapshot saved"}


@router.post("/calculate/{game_id}")
def calculate_edge(game_id: int, db: Session = Depends(get_db)):
    """Run edge calculation for a single game. Requires prediction + odds to exist."""
    result = calculate_edge_for_game(db, game_id)
    if not result:
        raise HTTPException(status_code=404, detail="Prediction or odds not found for this game")
    return result


@router.post("/calculate-all")
def calculate_all(db: Session = Depends(get_db)):
    """Recalculate edges for all of today's games in one call."""
    results = calculate_all_edges_today(db)
    return {"calculated": len(results), "results": results}


@router.get("/today")
def edges_today(
    min_edge: float = 0.02,
    tier: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Core endpoint. Returns today's edges sorted by EV descending.
    Optionally filter by minimum edge or confidence tier.
    """
    today = datetime.now(ZoneInfo("America/New_York")).date()

    query = (
        db.query(EdgeResult, Game)
        .join(Game, EdgeResult.game_id == Game.game_id)
        .filter(Game.game_date == today)
        .filter(EdgeResult.edge_pct >= min_edge)
    )
    if tier:
        query = query.filter(EdgeResult.confidence_tier == tier)

    rows = query.order_by(desc(EdgeResult.ev_away + EdgeResult.ev_home)).all()

    return [_format_edge_response(edge, game, db) for edge, game in rows]


def _format_edge_response(edge: EdgeResult, game: Game, db: Session) -> dict:
    movement = (
        db.query(LineMovement).filter(LineMovement.id == edge.movement_id).first()
        if edge.movement_id
        else None
    )

    response = {
        "game_id": edge.game_id,
        "matchup": f"{game.away_team} @ {game.home_team}",
        "game_time": game.start_time,
        # Model
        "model_away_win_pct": float(edge.model_away_win_pct),
        "model_home_win_pct": float(edge.model_home_win_pct),
        # Sportsbook
        "implied_away_pct": float(edge.implied_away_pct),
        "implied_home_pct": float(edge.implied_home_pct),
        # Edge & EV
        "edge_away": float(edge.edge_away),
        "edge_home": float(edge.edge_home),
        "ev_away": float(edge.ev_away),
        "ev_home": float(edge.ev_home),
        # Totals
        "model_total": float(edge.model_total) if edge.model_total else None,
        "book_total": float(edge.book_total) if edge.book_total else None,
        "ev_over": float(edge.ev_over),
        "ev_under": float(edge.ev_under),
        # Decision
        "recommended_play": edge.recommended_play,
        "confidence_tier": edge.confidence_tier,
        "edge_pct": float(edge.edge_pct),
    }

    if movement:
        response["line_movement"] = {
            "open_away_ml": movement.open_away_ml,
            "open_home_ml": movement.open_home_ml,
            "pregame_away_ml": movement.pregame_away_ml,
            "pregame_home_ml": movement.pregame_home_ml,
            "away_prob_move": float(movement.away_prob_move) if movement.away_prob_move else None,
            "home_prob_move": float(movement.home_prob_move) if movement.home_prob_move else None,
            "total_move": float(movement.total_move) if movement.total_move else None,
            "sharp_away": movement.sharp_away,
            "sharp_home": movement.sharp_home,
            "total_steam_over": movement.total_steam_over,
            "total_steam_under": movement.total_steam_under,
        }
        response["movement_boost"] = float(edge.movement_boost) if edge.movement_boost else 0.0

    return response
