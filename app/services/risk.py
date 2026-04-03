"""
Risk control layer for bet execution.

evaluate_bet_for_execution() is the single gate that every bet must pass
before it reaches the provider. All decisions are logged — no silent failures.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    approved: bool
    reasons: list[str] = field(default_factory=list)
    recommended_stake: float = 0.0
    capped_stake: float = 0.0
    expected_value_dollars: float = 0.0


def evaluate_bet_for_execution(
    bet: dict,
    daily_stats: dict,
    bankroll_state: float,
    requested_stake: float,
    provider_mode: str = "paper",
) -> RiskDecision:
    """
    Evaluate whether a candidate bet should be executed.

    Args:
        bet: ranked bet dict — must include ev, edge_pct, confidence, play, game_id
        daily_stats: {"bets_placed_today": int, "total_risked_today": float}
        bankroll_state: current available balance
        requested_stake: proposed stake in dollars
        provider_mode: "paper" | "live"

    Returns:
        RiskDecision with approved flag, all reasons, and adjusted stake
    """
    # Import here to allow config to be loaded from .env at runtime
    from app.config import (
        ALLOWED_MARKET_TYPES,
        BETTING_ENABLED,
        BETTING_MODE,
        FLAT_STAKE,
        MAX_BETS_PER_DAY,
        MAX_DAILY_RISK,
        MAX_STAKE_PER_BET,
        MIN_EDGE,
        MIN_EV,
        REQUIRE_CONFIDENCE,
        SLIPPAGE_TOLERANCE,
    )
    from app.services.kill_switch import is_kill_switch_active

    block_reasons: list[str] = []
    info_reasons: list[str] = []

    # ── Hard kill switch ──────────────────────────────────────────────────
    if is_kill_switch_active():
        r = "KILL_SWITCH is active — all bet execution blocked"
        logger.warning("RISK BLOCK | %s", r)
        return RiskDecision(approved=False, reasons=[r])

    # ── Live mode guard (paper is always permitted past this) ─────────────
    if BETTING_MODE == "live" or provider_mode == "live":
        if not BETTING_ENABLED:
            r = "BETTING_ENABLED=false — live bets require explicit opt-in"
            block_reasons.append(r)
        if BETTING_MODE != "live":
            r = "BETTING_MODE != live"
            block_reasons.append(r)
        if provider_mode == "paper":
            r = "Provider is paper — cannot place live bets through paper provider"
            block_reasons.append(r)

    if BETTING_MODE == "disabled":
        r = "BETTING_MODE=disabled"
        logger.warning("RISK BLOCK | %s", r)
        return RiskDecision(approved=False, reasons=[r])

    # ── EV floor ─────────────────────────────────────────────────────────
    ev = float(bet.get("ev") or 0)
    if ev < MIN_EV:
        block_reasons.append(f"EV {ev:.4f} < MIN_EV {MIN_EV:.4f}")

    # ── Edge floor ────────────────────────────────────────────────────────
    edge = float(bet.get("edge_pct") or 0)
    if edge < MIN_EDGE:
        block_reasons.append(f"Edge {edge:.4f} < MIN_EDGE {MIN_EDGE:.4f}")

    # ── Confidence requirement ────────────────────────────────────────────
    conf = (bet.get("confidence") or "").lower().strip()
    required_conf = (REQUIRE_CONFIDENCE or "").lower().strip()
    if required_conf and conf != required_conf:
        block_reasons.append(
            f"Confidence '{conf}' != required '{required_conf}'"
        )

    # ── Market type allowlist ─────────────────────────────────────────────
    play = (bet.get("play") or "").lower()
    if "_ml" in play:
        market = "moneyline"
    elif play in ("over", "under"):
        market = "total"
    else:
        market = "spread"

    if ALLOWED_MARKET_TYPES and market not in ALLOWED_MARKET_TYPES:
        block_reasons.append(
            f"Market '{market}' not in allowed list {sorted(ALLOWED_MARKET_TYPES)}"
        )

    # ── Daily bet count cap ───────────────────────────────────────────────
    bets_today = int(daily_stats.get("bets_placed_today", 0))
    if MAX_BETS_PER_DAY and bets_today >= MAX_BETS_PER_DAY:
        block_reasons.append(
            f"Daily bet cap reached: {bets_today}/{MAX_BETS_PER_DAY}"
        )

    # ── Daily risk cap ────────────────────────────────────────────────────
    risked_today = float(daily_stats.get("total_risked_today", 0))
    if risked_today + requested_stake > MAX_DAILY_RISK:
        block_reasons.append(
            f"Daily risk cap: ${risked_today:.2f} + ${requested_stake:.2f} "
            f"> MAX_DAILY_RISK ${MAX_DAILY_RISK:.2f}"
        )

    # ── Per-bet stake cap (soft — caps, does not block) ───────────────────
    capped_stake = min(requested_stake, MAX_STAKE_PER_BET)
    if capped_stake < requested_stake:
        info_reasons.append(
            f"Stake capped: ${requested_stake:.2f} → ${capped_stake:.2f} "
            f"(MAX_STAKE_PER_BET=${MAX_STAKE_PER_BET:.2f})"
        )

    # ── Bankroll check ────────────────────────────────────────────────────
    if capped_stake > bankroll_state:
        block_reasons.append(
            f"Insufficient balance: need ${capped_stake:.2f}, "
            f"have ${bankroll_state:.2f}"
        )

    all_reasons = block_reasons + info_reasons
    approved = len(block_reasons) == 0
    ev_dollars = ev * capped_stake if approved else 0.0

    if approved:
        logger.info(
            "RISK APPROVED | game=%s side=%s stake=%.2f ev=%.4f edge=%.4f conf=%s%s",
            bet.get("game_id"),
            bet.get("play"),
            capped_stake,
            ev,
            edge,
            conf,
            f" [{'; '.join(info_reasons)}]" if info_reasons else "",
        )
    else:
        logger.info(
            "RISK REJECTED | game=%s side=%s — %s",
            bet.get("game_id"),
            bet.get("play"),
            "; ".join(block_reasons),
        )

    return RiskDecision(
        approved=approved,
        reasons=all_reasons,
        recommended_stake=requested_stake,
        capped_stake=capped_stake,
        expected_value_dollars=ev_dollars,
    )
