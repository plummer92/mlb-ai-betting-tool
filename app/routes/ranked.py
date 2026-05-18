from __future__ import annotations

import logging
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
from app.services.edge_service import get_trustworthy_active_edges
from app.services.market_respect_service import market_respect_adjustment, market_respect_for_edge
from app.services.odds_service import odds_freshness_metadata

router = APIRouter(prefix="/api/ranked", tags=["ranked"])
logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
FINAL_STATUSES = {"Final", "Completed Early", "Cancelled"}
DECISION_ORDER = {
    "FIRE": 0,
    "WATCH": 1,
    "WAIT FOR ODDS": 2,
    "BLOCKED": 3,
    "NO BET": 4,
}


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


def _pick_odds(edge: EdgeResult, odds: GameOdds | None) -> int | None:
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


def _build_ranked_rows(
    db: Session,
    limit: int = 10,
    active_only: bool = True,
) -> list[dict]:
    today = datetime.now(ET).date()

    rows = get_trustworthy_active_edges(db, game_date=today)

    latest_by_game: dict[int, tuple[EdgeResult, Game, GameOdds | None]] = {}

    for edge, game, _prediction, odds in rows:
        if active_only and (game.status in FINAL_STATUSES):
            continue
        if edge.game_id not in latest_by_game:
            latest_by_game[edge.game_id] = (edge, game, odds)

    ranked = []
    for edge, game, odds in latest_by_game.values():
        ev = _pick_ev(edge)
        market_respect = market_respect_for_edge(db, edge, odds=odds, game=game)
        adjustment = market_respect_adjustment(
            edge_pct=float(edge.edge_pct or 0),
            ev=ev,
            confidence=edge.confidence_tier,
            market_respect=market_respect,
            odds_american=_pick_odds(edge, odds),
        )
        freshness = odds_freshness_metadata(db, game=game, odds_row=odds)
        odds_fresh = freshness["status"] in {"fresh", "quiet_market"}
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
                "raw_edge_pct": adjustment["raw_edge_pct"],
                "raw_ev": adjustment["raw_ev"],
                "adjusted_edge_pct": adjustment["adjusted_edge_pct"],
                "adjusted_ev": adjustment["adjusted_ev"],
                "adjusted_confidence": adjustment["adjusted_confidence"],
                "adjusted_kelly_fraction": adjustment["adjusted_kelly_fraction"],
                "confidence": edge.confidence_tier,
                "sportsbook": odds.sportsbook if odds else None,
                "snapshot_type": odds.snapshot_type.value if odds and odds.snapshot_type else None,
                "odds_fresh": odds_fresh,
                "odds_freshness_status": freshness["status"].upper(),
                "odds_freshness": freshness,
                "movement_direction": edge.movement_direction,
                "market_respect": market_respect,
                "market_respect_score": market_respect["score"],
                "market_respect_tags": market_respect["tags"],
                "market_trust_bucket": adjustment["bucket"],
                "market_respect_adjustment": adjustment,
                "market_respect_alert_allowed": adjustment["alert_allowed"],
                "calculated_at": edge.calculated_at.isoformat() if edge.calculated_at else None,
                "policy_qualified": qualifies_for_bet_policy(
                    play=edge.recommended_play,
                    edge_pct=adjustment["adjusted_edge_pct"],
                    ev=adjustment["adjusted_ev"],
                    confidence=adjustment["adjusted_confidence"],
                ),
            }
        )

    ranked.sort(key=lambda x: (x["adjusted_edge_pct"], x["adjusted_ev"], x["edge_pct"]), reverse=True)

    for i, row in enumerate(ranked, start=1):
        row["rank"] = i

    limited = ranked[:limit]
    if not limited:
        logger.warning(
            "No plays survived decision pipeline",
            extra={
                "event": "decision_pipeline_zero_survivors",
                "decision_pipeline": {
                    "surface": "ranked_bets",
                    "game_date": today.isoformat(),
                    "active_only": active_only,
                    "trusted_edges": len(rows),
                },
            },
        )

    return limited


def _decision_reason(row: dict, status: str) -> str:
    raw = float(row.get("raw_edge_pct", row.get("edge_pct") or 0))
    adjusted = float(row.get("adjusted_edge_pct") or 0)
    tag = (row.get("market_respect_tags") or ["MARKET NEUTRAL"])[0]
    direction = row.get("movement_direction") or "no recorded movement"
    base = (
        f"Raw edge was {raw * 100:.1f}%, adjusted to {adjusted * 100:.1f}% "
        f"because {tag.lower()} and {direction.replace('_', ' ')}."
    )
    if status == "FIRE":
        return base + " Trust is high and odds are fresh."
    if status == "WATCH":
        return base + " Edge is positive, but market trust is moderate or sample is thin."
    if status == "WAIT FOR ODDS":
        return base + " Waiting because odds are stale or the open/close read is incomplete."
    if status == "BLOCKED":
        return base + " Blocked because the market rejected the model side."
    return base + " No bet because the adjusted edge is not positive."


def _decision_row_from_ranked(row: dict) -> dict:
    adjustment = row.get("market_respect_adjustment") or {}
    tags = row.get("market_respect_tags") or adjustment.get("tags") or []
    score = int(row.get("market_respect_score", adjustment.get("score", 50)))
    adjusted_edge = float(row.get("adjusted_edge_pct") or 0)
    freshness_status = row.get("odds_freshness_status")
    stale = "STALE OPEN" in tags or freshness_status in {"STALE_FEED", "STALE_OPEN", "MISSING_ODDS"}
    rejected = "MARKET REJECTED" in tags or row.get("market_trust_bucket") == "market_rejection"

    if adjusted_edge <= 0:
        status = "NO BET"
    elif stale:
        status = "WAIT FOR ODDS"
    elif rejected:
        status = "BLOCKED"
    elif score >= 70:
        status = "FIRE"
    else:
        status = "WATCH"

    reason = _decision_reason(row, status)
    return {
        "rank": row.get("rank"),
        "game_id": row.get("game_id"),
        "game": row.get("matchup"),
        "matchup": row.get("matchup"),
        "play": row.get("play"),
        "raw_edge_pct": row.get("raw_edge_pct", row.get("edge_pct")),
        "adjusted_edge_pct": row.get("adjusted_edge_pct"),
        "market_trust_score": score,
        "market_respect_tag": tags[0] if tags else "MARKET NEUTRAL",
        "market_respect_tags": tags,
        "odds_freshness_status": row.get("odds_freshness_status", "UNKNOWN"),
        "decision_status": status,
        "decision_reason": reason,
        "sportsbook": row.get("sportsbook"),
        "start_time": row.get("start_time"),
        "market_respect": row.get("market_respect"),
        "market_respect_adjustment": adjustment,
    }


def _build_decision_queue(db: Session, limit: int = 20, active_only: bool = True) -> list[dict]:
    rows = [_decision_row_from_ranked(row) for row in _build_ranked_rows(db=db, limit=50, active_only=active_only)]
    rows.sort(
        key=lambda row: (
            DECISION_ORDER.get(row["decision_status"], 99),
            -(float(row.get("adjusted_edge_pct") or 0)),
        )
    )
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    limited = rows[:limit]
    if not limited:
        logger.warning(
            "No plays survived decision pipeline",
            extra={
                "event": "decision_pipeline_zero_survivors",
                "decision_pipeline": {
                    "surface": "decision_queue",
                    "active_only": active_only,
                },
            },
        )
    return limited


def _build_discord_lines(bets: list[dict], title: str = "📊 **Ranked MLB Bets**") -> list[str]:
    lines = [title]
    for bet in bets:
        snap = f" [{bet['snapshot_type']}]" if bet.get("snapshot_type") else ""
        move = f" ↕{bet['movement_direction']}" if bet.get("movement_direction") else ""
        lines.append(
            f"#{bet['rank']} {bet['matchup']} | {bet['play']}{snap}{move} | "
            f"edge={bet['edge_pct']:.4f}->{bet.get('adjusted_edge_pct', bet['edge_pct']):.4f} | "
            f"ev={bet['ev']:.4f}->{bet.get('adjusted_ev', bet['ev']):.4f} | "
            f"{bet.get('adjusted_confidence') or bet['confidence'] or 'n/a'} | MRS={bet.get('market_respect_score', 50)} "
            f"{','.join(bet.get('market_respect_tags', [])[:2])}"
        )
    return lines


def _alertable_ranked_bets(bets: list[dict]) -> list[dict]:
    return [
        bet
        for bet in bets
        if bet.get("market_respect_alert_allowed")
        and float(bet.get("adjusted_edge_pct") or 0) > 0
        and float(bet.get("adjusted_ev") or 0) > 0
    ]


@router.get("/bets")
def get_ranked_bets(
    limit: int = Query(10, ge=1, le=50),
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
):
    return _build_ranked_rows(db=db, limit=limit, active_only=active_only)


@router.get("/decision-queue")
def get_decision_queue(
    limit: int = Query(20, ge=1, le=50),
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
):
    return _build_decision_queue(db=db, limit=limit, active_only=active_only)


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

    bets = _alertable_ranked_bets(_build_ranked_rows(db=db, limit=50, active_only=active_only))[:limit]
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
    if not bet.get("market_respect_alert_allowed"):
        raise HTTPException(status_code=409, detail="Market respect gate suppressed this alert")
    snap = f" [{bet['snapshot_type']}]" if bet.get("snapshot_type") else ""
    move = f" ↕{bet['movement_direction']}" if bet.get("movement_direction") else ""
    content = (
        f"⚾ **Pregame Alert** — {bet['matchup']}{snap}\n"
        f"Play: **{bet['play']}**{move} | edge={bet['edge_pct']:.4f}->{bet['adjusted_edge_pct']:.4f} | "
        f"ev={bet['ev']:.4f}->{bet['adjusted_ev']:.4f} | {bet['adjusted_confidence'] or bet['confidence'] or 'n/a'}\n"
        f"MRS: {bet.get('market_respect_score', 50)} | {', '.join(bet.get('market_respect_tags', [])[:2])}\n"
        f"Start: {bet['start_time']} | {bet.get('away_probable_pitcher', '?')} vs {bet.get('home_probable_pitcher', '?')}"
    )

    payload = {"content": content}
    resp = requests.post(webhook, json=payload, timeout=15)
    resp.raise_for_status()

    return {"sent": 1, "status_code": resp.status_code, "game_id": game_id}
