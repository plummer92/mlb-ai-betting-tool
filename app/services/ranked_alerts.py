from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from app.db import SessionLocal
from app.models.schema import EdgeResult, Game, GameOdds
from app.services.decision_journal_service import send_daily_trade_summary
from app.services.edge_service import get_trustworthy_active_edges
from app.services.market_respect_service import classify_tradable_signal, market_respect_adjustment, market_respect_for_edge
from app.services.totals_policy_service import apply_under_cluster_risk, evaluate_totals_policy

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


def _build_ranked_rows(limit: int = 10, active_only: bool = True) -> list[dict]:
    db = SessionLocal()
    try:
        today = datetime.now(ET).date()

        rows = get_trustworthy_active_edges(db, game_date=today)

        latest_by_game: dict[int, tuple] = {}

        for edge, game, prediction, odds in rows:
            if active_only and (game.status in FINAL_STATUSES):
                continue
            if edge.game_id not in latest_by_game:
                latest_by_game[edge.game_id] = (edge, game, prediction, odds)

        ranked = []
        for edge, game, prediction, odds in latest_by_game.values():
            ev = _pick_ev(edge)
            market_respect = market_respect_for_edge(db, edge, odds=odds, game=game)
            adjustment = market_respect_adjustment(
                edge_pct=float(edge.edge_pct or 0),
                ev=ev,
                confidence=edge.confidence_tier,
                market_respect=market_respect,
                odds_american=_pick_odds(edge, odds),
            )
            tradable = classify_tradable_signal(
                market_respect=market_respect,
                market_adjustment=adjustment,
                raw_edge_pct=adjustment["raw_edge_pct"],
                raw_ev=adjustment["raw_ev"],
                adjusted_confidence=adjustment["adjusted_confidence"],
            )
            totals_policy = evaluate_totals_policy(
                db,
                edge=edge,
                game=game,
                prediction=prediction,
                odds=odds,
                market_respect=market_respect,
            )
            ranked.append(
                {
                    "game_id": game.game_id,
                    "matchup": f"{game.away_team} @ {game.home_team}",
                    "status": game.status,
                    "start_time": game.start_time,
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
                    "movement_direction": edge.movement_direction,
                    "market_respect": market_respect,
                    "market_respect_score": market_respect["score"],
                    "market_respect_tags": market_respect["tags"],
                    "market_trust_bucket": adjustment["bucket"],
                    "market_respect_adjustment": adjustment,
                    "market_respect_alert_allowed": adjustment["alert_allowed"],
                    "tradable_signal": tradable["tradable_signal"],
                    "tradable_reason": tradable["tradable_reason"],
                    "trade_allowed": tradable["trade_allowed"],
                    "totals_policy": totals_policy,
                    "totals_policy_score": totals_policy["totals_policy_score"],
                    "policy_status": totals_policy["policy_status"],
                    "policy_reason": totals_policy["policy_reason"],
                    "totals_policy_alert_allowed": totals_policy["alert_allowed"],
                }
            )

        cluster = apply_under_cluster_risk(ranked)
        ranked.sort(
            key=lambda x: (
                0 if x.get("policy_status") in {"APPROVED", "CLUSTER_RISK"} else 1 if x.get("policy_status") == "CAUTION" else 2,
                -float(x.get("totals_policy_score", 50)),
                -float(x["adjusted_edge_pct"]),
                -float(x["adjusted_ev"]),
                -float(x["edge_pct"]),
            )
        )

        for i, row in enumerate(ranked, start=1):
            row["rank"] = i
            row["totals_cluster"] = cluster

        return ranked[:limit]
    finally:
        db.close()


def _alertable_ranked_bets(bets: list[dict]) -> list[dict]:
    return [
        bet
        for bet in bets
        if bet.get("trade_allowed")
        and bet.get("tradable_signal") == "TRADE"
        and bet.get("market_respect_alert_allowed")
        and bet.get("totals_policy_alert_allowed", True)
        and bet.get("policy_status") != "BLOCKED"
        and float(bet.get("adjusted_edge_pct") or 0) > 0
        and float(bet.get("adjusted_ev") or 0) > 0
    ]


def send_ranked_bets_to_discord_job(limit: int = 10, active_only: bool = True) -> dict:
    db = SessionLocal()
    try:
        return send_daily_trade_summary(db=db, limit=max(limit, 50), active_only=active_only)
    finally:
        db.close()

    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        return {"sent": 0, "error": "DISCORD_WEBHOOK_URL is not set"}

    bets = _alertable_ranked_bets(_build_ranked_rows(limit=50, active_only=active_only))[:limit]
    if not bets:
        return {"sent": 0, "message": "No ranked bets found"}

    lines = ["📊 **Daily Ranked MLB Bets**"]
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

    payload = {"content": "\n".join(lines)}
    resp = requests.post(webhook, json=payload, timeout=15)
    resp.raise_for_status()

    return {"sent": len(bets), "status_code": resp.status_code}
