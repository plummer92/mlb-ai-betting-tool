from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from app.db import SessionLocal
from app.models.schema import EdgeResult, Game, GameOdds
from app.services.edge_service import get_trustworthy_active_edges

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


def _build_ranked_rows(limit: int = 10, active_only: bool = True) -> list[dict]:
    db = SessionLocal()
    try:
        today = datetime.now(ET).date()

        rows = get_trustworthy_active_edges(db, game_date=today)

        latest_by_game: dict[int, tuple] = {}

        for edge, game, _prediction, odds in rows:
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
                    "matchup": f"{game.away_team} @ {game.home_team}",
                    "status": game.status,
                    "start_time": game.start_time,
                    "play": edge.recommended_play,
                    "edge_pct": float(edge.edge_pct or 0),
                    "ev": ev,
                    "confidence": edge.confidence_tier,
                    "sportsbook": odds.sportsbook if odds else None,
                    "snapshot_type": odds.snapshot_type.value if odds and odds.snapshot_type else None,
                    "movement_direction": edge.movement_direction,
                }
            )

        ranked.sort(key=lambda x: (x["edge_pct"], x["ev"]), reverse=True)

        for i, row in enumerate(ranked, start=1):
            row["rank"] = i

        return ranked[:limit]
    finally:
        db.close()


def send_ranked_bets_to_discord_job(limit: int = 10, active_only: bool = True) -> dict:
    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        return {"sent": 0, "error": "DISCORD_WEBHOOK_URL is not set"}

    bets = _build_ranked_rows(limit=limit, active_only=active_only)
    if not bets:
        return {"sent": 0, "message": "No ranked bets found"}

    lines = ["📊 **Daily Ranked MLB Bets**"]
    for bet in bets:
        snap = f" [{bet['snapshot_type']}]" if bet.get("snapshot_type") else ""
        move = f" ↕{bet['movement_direction']}" if bet.get("movement_direction") else ""
        lines.append(
            f"#{bet['rank']} {bet['matchup']} | {bet['play']}{snap}{move} | "
            f"edge={bet['edge_pct']:.4f} | ev={bet['ev']:.4f} | "
            f"{bet['confidence'] or 'n/a'}"
        )

    payload = {"content": "\n".join(lines)}
    resp = requests.post(webhook, json=payload, timeout=15)
    resp.raise_for_status()

    return {"sent": len(bets), "status_code": resp.status_code}
