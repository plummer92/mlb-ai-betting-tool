"""
Bet execution service — orchestrates the full pipeline from candidate
selection to paper placement to settlement.

IMPORTANT: This service only READS from existing pipeline tables
(games, edge_results, game_odds). It WRITES only to bet_orders,
bet_executions, and bankroll_snapshots.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.betting import BetExecution, BetOrder, BankrollSnapshot
from app.models.schema import EdgeResult, Game, GameOdds
from app.services.books.base import BetRequest
from app.services.books.factory import get_provider
from app.services.risk import evaluate_bet_for_execution
from app.services.staking import compute_stake

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

# Elite bet thresholds (mirrors dashboard isBettable logic)
_MIN_EV = 0.10
_MIN_EDGE = 0.10
_REQ_CONF = "strong"

FINAL_STATUSES = {"Final", "Completed Early", "Cancelled"}


# ── Candidate selection ───────────────────────────────────────────────────────

def create_candidate_bets_for_today(db: Session) -> list[dict]:
    """
    Return elite bet candidates for today — READ ONLY from existing tables.
    Does not create any DB records.
    """
    # Import here to avoid circular import (ranked imports from db/models)
    from app.routes.ranked import _build_ranked_rows
    all_bets = _build_ranked_rows(db=db, limit=50, active_only=True)
    candidates = [b for b in all_bets if _is_elite(b)]
    logger.info(
        "Candidates | total=%d elite=%d (EV≥%.2f edge≥%.2f conf=%s)",
        len(all_bets), len(candidates), _MIN_EV, _MIN_EDGE, _REQ_CONF,
    )
    return candidates


def _is_elite(bet: dict) -> bool:
    return (
        float(bet.get("ev") or 0) >= _MIN_EV
        and float(bet.get("edge_pct") or 0) >= _MIN_EDGE
        and (bet.get("confidence") or "").lower().strip() == _REQ_CONF
    )


# ── Daily stats ───────────────────────────────────────────────────────────────

def _get_daily_stats(db: Session) -> dict:
    today = datetime.now(ET).date()
    orders_today = (
        db.query(BetOrder)
        .join(Game, BetOrder.game_id == Game.game_id)
        .filter(
            Game.game_date == today,
            BetOrder.status.in_(["placed", "placed_paper", "settled"]),
        )
        .all()
    )
    return {
        "bets_placed_today": len(orders_today),
        "total_risked_today": sum(float(o.requested_stake or 0) for o in orders_today),
    }


# ── Odds lookup ───────────────────────────────────────────────────────────────

def _get_odds_and_line(db: Session, game_id: int, side: str) -> tuple[int, float | None]:
    """Return (american_odds, total_line_or_None) from game_odds for this game+side."""
    odds_row = (
        db.query(GameOdds)
        .filter(GameOdds.game_id == game_id)
        .order_by(GameOdds.fetched_at.desc())
        .first()
    )
    if not odds_row:
        return -110, None

    if side == "away_ml":
        return int(odds_row.away_ml or -110), None
    if side == "home_ml":
        return int(odds_row.home_ml or -110), None
    if side == "over":
        return int(odds_row.over_odds or -110), float(odds_row.total_line or 0) or None
    if side == "under":
        return int(odds_row.under_odds or -110), float(odds_row.total_line or 0) or None
    return -110, None


# ── Paper execution ───────────────────────────────────────────────────────────

def execute_paper_bets_for_today(db: Session) -> dict:
    """
    Full paper execution pipeline:
    1. Fetch today's elite candidates (read-only)
    2. Run risk controls
    3. Place via paper provider (local DB writes only)
    4. Return summary

    Safe to call multiple times — skips duplicate game+side orders.
    """
    from app.config import BETTING_MODE

    if BETTING_MODE == "live":
        msg = "execute_paper_bets called but BETTING_MODE=live — use live endpoint"
        logger.warning(msg)
        return {"error": msg}

    provider = get_provider(db)
    bankroll = provider.get_balance().available

    candidates = create_candidate_bets_for_today(db)
    daily_stats = _get_daily_stats(db)

    results: dict[str, list] = {"approved": [], "rejected": [], "errors": []}

    for bet in candidates:
        game_id = bet["game_id"]
        side = bet["play"]
        ev = float(bet.get("ev") or 0)
        edge = float(bet.get("edge_pct") or 0)

        # ── Idempotency: skip if active order already exists ──
        existing = (
            db.query(BetOrder)
            .filter(
                BetOrder.game_id == game_id,
                BetOrder.side == side,
                BetOrder.status.in_(["placed_paper", "placed", "approved"]),
            )
            .first()
        )
        if existing:
            logger.debug("Duplicate skipped: game=%s side=%s order_id=%s", game_id, side, existing.id)
            continue

        odds_american, total_line = _get_odds_and_line(db, game_id, side)
        raw_stake = compute_stake(ev, edge, odds_american, bankroll)
        decision = evaluate_bet_for_execution(
            bet, daily_stats, bankroll, raw_stake, provider_mode="paper"
        )

        market_type = (
            "moneyline" if "_ml" in side
            else "total" if side in ("over", "under")
            else "spread"
        )

        # ── Persist order regardless of outcome ──
        order = BetOrder(
            game_id=game_id,
            sportsbook="paper",
            provider_mode="paper",
            market_type=market_type,
            side=side,
            requested_line=total_line,
            requested_odds=odds_american,
            requested_stake=decision.capped_stake or raw_stake,
            edge_pct=edge,
            ev=ev,
            confidence=bet.get("confidence"),
            source_rank=bet.get("rank"),
            status="rejected" if not decision.approved else "approved",
            rejection_reason="; ".join(decision.reasons) if not decision.approved else None,
        )
        db.add(order)
        db.flush()  # get order.id

        if not decision.approved:
            db.commit()
            results["rejected"].append({
                "game_id": game_id,
                "side": side,
                "reasons": decision.reasons,
            })
            continue

        # ── Get quote ──
        bet_request = BetRequest(
            game_id=game_id,
            event_id=str(game_id),
            market_type=market_type,
            side=side,
            line=total_line,
            odds_american=odds_american,
            stake=float(decision.capped_stake),
            confidence=bet.get("confidence", ""),
            edge_pct=edge,
            ev=ev,
        )

        try:
            quote = provider.get_quote(bet_request)

            if not quote.available:
                order.status = "failed"
                order.rejection_reason = "Market unavailable at quote time"
                db.commit()
                results["errors"].append({"game_id": game_id, "side": side, "error": "market unavailable"})
                continue

            # ── Place ──
            placement = provider.place_bet(bet_request)

            if placement.success:
                order.status = "placed_paper"
                db.flush()

                exec_rec = BetExecution(
                    bet_order_id=order.id,
                    external_bet_id=placement.external_bet_id,
                    placed_odds=placement.placed_odds,
                    placed_line=placement.placed_line,
                    placed_stake=placement.placed_stake,
                    placed_at=datetime.now(timezone.utc),
                    fill_status=placement.fill_status,
                    raw_response_json=json.dumps(placement.raw_response),
                )
                db.add(exec_rec)

                # Debit bankroll snapshot
                new_bal = bankroll - float(decision.capped_stake)
                provider.snapshot_bankroll(new_bal)
                bankroll = new_bal

                daily_stats["bets_placed_today"] += 1
                daily_stats["total_risked_today"] += float(decision.capped_stake)

                db.commit()
                results["approved"].append({
                    "order_id": order.id,
                    "game_id": game_id,
                    "side": side,
                    "stake": float(decision.capped_stake),
                    "odds": placement.placed_odds,
                    "external_id": placement.external_bet_id,
                    "ev": ev,
                    "edge": edge,
                })
            else:
                order.status = "failed"
                order.rejection_reason = placement.message
                db.commit()
                results["errors"].append({"game_id": game_id, "side": side, "error": placement.message})

        except Exception as exc:
            logger.exception("Execution error: game=%s side=%s", game_id, side)
            order.status = "failed"
            order.rejection_reason = str(exc)
            try:
                db.commit()
            except Exception:
                db.rollback()
            results["errors"].append({"game_id": game_id, "side": side, "error": str(exc)})

    return {
        "mode": "paper",
        "candidates": len(candidates),
        "approved": len(results["approved"]),
        "rejected": len(results["rejected"]),
        "errors": len(results["errors"]),
        "detail": results,
    }


# ── Settlement ────────────────────────────────────────────────────────────────

def settle_paper_bets(db: Session) -> dict:
    """
    Settle open paper bets using final scores from the games table.
    Only settles games where final_away_score and final_home_score are populated.
    """
    open_pairs = (
        db.query(BetOrder, BetExecution)
        .join(BetExecution, BetExecution.bet_order_id == BetOrder.id)
        .filter(
            BetOrder.provider_mode == "paper",
            BetOrder.status == "placed_paper",
        )
        .all()
    )

    settled_results = []

    for order, execution in open_pairs:
        game = db.query(Game).filter(Game.game_id == order.game_id).first()
        if not game:
            continue
        if game.final_away_score is None or game.final_home_score is None:
            continue  # game not yet complete

        away = game.final_away_score
        home = game.final_home_score
        side = order.side
        odds = execution.placed_odds or order.requested_odds or -110
        stake = float(order.requested_stake or 0)
        if stake == 0:
            continue

        outcome = _determine_outcome(side, away, home, order)
        if outcome is None:
            logger.warning("Could not determine outcome for order %s side=%s", order.id, side)
            continue

        pl_units, pl_dollars = _compute_pl(outcome, int(odds), stake)

        execution.settled_result = outcome
        execution.profit_loss_units = pl_units
        execution.profit_loss_dollars = pl_dollars
        execution.settled_at = datetime.now(timezone.utc)
        order.status = "settled"

        settled_results.append({
            "order_id": order.id,
            "game_id": order.game_id,
            "side": side,
            "result": outcome,
            "stake": stake,
            "odds": odds,
            "pl_dollars": pl_dollars,
        })
        logger.info(
            "SETTLED | order=%s game=%s side=%s result=%s pl=$%.2f",
            order.id, order.game_id, side, outcome, pl_dollars,
        )

    if settled_results:
        db.commit()
        # Credit bankroll for net P/L
        provider = get_provider(db)
        current_bal = provider.get_balance().available
        net = sum(r["pl_dollars"] for r in settled_results)
        # Also add back the stakes on winning/push bets
        net_balance = current_bal + sum(
            r["stake"] + r["pl_dollars"] if r["result"] in ("win", "push")
            else 0
            for r in settled_results
        )
        provider.snapshot_bankroll(net_balance)
    else:
        db.commit()

    return {
        "settled": len(settled_results),
        "net_pl_dollars": sum(r["pl_dollars"] for r in settled_results),
        "detail": settled_results,
    }


def _determine_outcome(side: str, away: int, home: int, order: BetOrder) -> str | None:
    if side == "away_ml":
        if away > home:
            return "win"
        if away == home:
            return "push"
        return "loss"

    if side == "home_ml":
        if home > away:
            return "win"
        if home == away:
            return "push"
        return "loss"

    # Totals — need the line from the order
    total_line = float(order.requested_line or 0)
    if total_line == 0:
        return None  # can't settle without a line
    actual_total = away + home

    if side == "over":
        if actual_total > total_line:
            return "win"
        if actual_total == total_line:
            return "push"
        return "loss"

    if side == "under":
        if actual_total < total_line:
            return "win"
        if actual_total == total_line:
            return "push"
        return "loss"

    return None


def _compute_pl(outcome: str, odds: int, stake: float) -> tuple[float, float]:
    if outcome == "push":
        return 0.0, 0.0
    if outcome == "loss":
        return -1.0, -stake
    # win
    if odds > 0:
        profit = stake * (odds / 100.0)
    else:
        profit = stake * (100.0 / abs(odds))
    return round(profit / stake, 4), round(profit, 2)


# ── Summary ───────────────────────────────────────────────────────────────────

def get_execution_summary(db: Session) -> dict:
    today = datetime.now(ET).date()

    all_pairs = (
        db.query(BetOrder, BetExecution)
        .outerjoin(BetExecution, BetExecution.bet_order_id == BetOrder.id)
        .filter(BetOrder.provider_mode == "paper")
        .all()
    )

    open_pairs = [(o, e) for o, e in all_pairs if o.status == "placed_paper"]
    settled_pairs = [(o, e) for o, e in all_pairs if o.status == "settled"]
    rejected_pairs = [(o, e) for o, e in all_pairs if o.status == "rejected"]

    def _in_today(o: BetOrder) -> bool:
        if not o.created_at:
            return False
        return o.created_at.astimezone(ET).date() == today

    pl_today = sum(
        float(e.profit_loss_dollars or 0)
        for o, e in settled_pairs
        if e and _in_today(o)
    )
    pl_all = sum(float(e.profit_loss_dollars or 0) for o, e in settled_pairs if e)

    provider = get_provider(db)
    balance = provider.get_balance().available

    # Wins / losses / pushes
    outcomes = [e.settled_result for _, e in settled_pairs if e and e.settled_result]
    wins = outcomes.count("win")
    losses = outcomes.count("loss")
    pushes = outcomes.count("push")

    settled_sorted = sorted(
        settled_pairs,
        key=lambda x: (x[1].settled_at if x[1] and x[1].settled_at else datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )

    from app.services.kill_switch import is_kill_switch_active

    return {
        "mode": "paper",
        "live_betting_enabled": False,
        "kill_switch_active": is_kill_switch_active(),
        "bankroll": round(balance, 2),
        "open_bets": len(open_pairs),
        "settled_bets": len(settled_pairs),
        "rejected_bets": len(rejected_pairs),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate": round(wins / max(wins + losses, 1), 4),
        "pl_today": round(pl_today, 2),
        "pl_all_time": round(pl_all, 2),
        "open": [
            {
                "order_id": o.id,
                "game_id": o.game_id,
                "side": o.side,
                "market_type": o.market_type,
                "stake": float(o.requested_stake or 0),
                "odds": o.requested_odds,
                "ev": float(o.ev or 0),
                "edge": float(o.edge_pct or 0),
                "confidence": o.confidence,
                "external_id": e.external_bet_id if e else None,
                "placed_at": e.placed_at.isoformat() if e and e.placed_at else None,
            }
            for o, e in open_pairs
        ],
        "settled_recent": [
            {
                "order_id": o.id,
                "game_id": o.game_id,
                "side": o.side,
                "stake": float(o.requested_stake or 0),
                "odds": o.requested_odds,
                "result": e.settled_result if e else None,
                "pl_dollars": float(e.profit_loss_dollars or 0) if e else None,
                "pl_units": float(e.profit_loss_units or 0) if e else None,
                "settled_at": e.settled_at.isoformat() if e and e.settled_at else None,
            }
            for o, e in settled_sorted[:10]
        ],
        "rejected_recent": [
            {
                "order_id": o.id,
                "game_id": o.game_id,
                "side": o.side,
                "ev": float(o.ev or 0),
                "edge": float(o.edge_pct or 0),
                "reasons": o.rejection_reason,
            }
            for o, _ in rejected_pairs[:10]
        ],
    }
