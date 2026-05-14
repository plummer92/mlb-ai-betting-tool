from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.middleware.auth import verify_api_key
from app.middleware.limiter import limiter
from app.models.schema import EdgeResult, Game, GameOdds
from app.services.betting_policy import qualifies_for_bet_policy

router = APIRouter(prefix="/api/ranked", tags=["ranked"])

ET = ZoneInfo("America/New_York")
FINAL_STATUSES = {"Final", "Completed Early", "Cancelled"}


def _pick_ev(edge: EdgeResult) -> float:
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


def _build_ranked_rows(
    db: Session,
    limit: int = 10,
    active_only: bool = True,
) -> list[dict]:
    today = datetime.now(ET).date()

    rows = (
        db.query(EdgeResult, Game, GameOdds)
        .join(Game, EdgeResult.game_id == Game.game_id)
        .outerjoin(GameOdds, EdgeResult.odds_id == GameOdds.id)
        .filter(Game.game_date == today)
        .order_by(EdgeResult.calculated_at.desc())
        .all()
    )

    latest_by_game: dict[int, tuple[EdgeResult, Game, GameOdds | None]] = {}

    for edge, game, odds in rows:
        if active_only and (game.status in FINAL_STATUSES):
            continue
        if edge.game_id not in latest_by_game:
            latest_by_game[edge.game_id] = (edge, game, odds)

    ranked = []
    for edge, game, odds in latest_by_game.values():
        ev = _pick_ev(edge)
        ranked.append(
            {
                "game_id": game.game_id,
                "away_team": game.away_team,
                "home_team": game.home_team,
                "matchup": f"{game.away_team} @ {game.home_team}",
                "venue": game.venue,
                "status": game.status,
                "start_time": game.start_time,
                "away_probable_pitcher": game.away_probable_pitcher,
                "home_probable_pitcher": game.home_probable_pitcher,
                "play": edge.recommended_play,
                "edge_pct": float(edge.edge_pct or 0),
                "ev": ev,
                "confidence": edge.confidence_tier,
                "sportsbook": odds.sportsbook if odds else None,
                "snapshot_type": odds.snapshot_type.value if odds and odds.snapshot_type else None,
                "movement_direction": edge.movement_direction,
                "calculated_at": edge.calculated_at.isoformat() if edge.calculated_at else None,
                "policy_qualified": qualifies_for_bet_policy(
                    play=edge.recommended_play,
                    edge_pct=float(edge.edge_pct or 0),
                    ev=ev,
                    confidence=edge.confidence_tier,
                ),
            }
        )

    ranked.sort(key=lambda x: (x["edge_pct"], x["ev"]), reverse=True)

    for i, row in enumerate(ranked, start=1):
        row["rank"] = i

    return ranked[:limit]


def _build_discord_lines(bets: list[dict], title: str = "📊 **Ranked MLB Bets**") -> list[str]:
    lines = [title]
    for bet in bets:
        snap = f" [{bet['snapshot_type']}]" if bet.get("snapshot_type") else ""
        move = f" ↕{bet['movement_direction']}" if bet.get("movement_direction") else ""
        lines.append(
            f"#{bet['rank']} {bet['matchup']} | {bet['play']}{snap}{move} | "
            f"edge={bet['edge_pct']:.4f} | ev={bet['ev']:.4f} | "
            f"{bet['confidence'] or 'n/a'}"
        )
    return lines


@router.get("/bets")
def get_ranked_bets(
    limit: int = Query(10, ge=1, le=50),
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
):
    return _build_ranked_rows(db=db, limit=limit, active_only=active_only)


@router.post("/discord", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def send_ranked_bets_to_discord(
    request: Request,
    limit: int = Query(10, ge=1, le=20),
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
):
    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        raise HTTPException(status_code=400, detail="DISCORD_WEBHOOK_URL is not set")

    bets = _build_ranked_rows(db=db, limit=limit, active_only=active_only)
    if not bets:
        return {"sent": 0, "message": "No ranked bets found"}

    lines = _build_discord_lines(bets)
    payload = {"content": "\n".join(lines)}
    resp = requests.post(webhook, json=payload, timeout=15)
    resp.raise_for_status()

    return {
        "sent": len(bets),
        "status_code": resp.status_code,
        "preview": lines[:3],
    }


@router.post("/discord/game/{game_id}", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def send_single_game_discord_alert(
    request: Request,
    game_id: int,
    db: Session = Depends(get_db),
):
    """Send a Discord alert for a single specific game (for manual triggers or testing)."""
    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        raise HTTPException(status_code=400, detail="DISCORD_WEBHOOK_URL is not set")

    all_bets = _build_ranked_rows(db=db, limit=50, active_only=False)
    match = [b for b in all_bets if b["game_id"] == game_id]
    if not match:
        raise HTTPException(status_code=404, detail=f"No edge data found for game {game_id} today")

    bet = match[0]
    snap = f" [{bet['snapshot_type']}]" if bet.get("snapshot_type") else ""
    move = f" ↕{bet['movement_direction']}" if bet.get("movement_direction") else ""
    content = (
        f"⚾ **Pregame Alert** — {bet['matchup']}{snap}\n"
        f"Play: **{bet['play']}**{move} | edge={bet['edge_pct']:.4f} | ev={bet['ev']:.4f} | {bet['confidence'] or 'n/a'}\n"
        f"Start: {bet['start_time']} | {bet.get('away_probable_pitcher', '?')} vs {bet.get('home_probable_pitcher', '?')}"
    )

    payload = {"content": content}
    resp = requests.post(webhook, json=payload, timeout=15)
    resp.raise_for_status()

    return {"sent": 1, "status_code": resp.status_code, "game_id": game_id}
