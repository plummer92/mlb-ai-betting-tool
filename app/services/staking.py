"""
Staking logic — computes the dollar stake for a given bet.

Modes (set via STAKING_MODE in .env):
  flat         — fixed dollar amount (FLAT_STAKE)
  kelly        — fractional Kelly based on EV and odds
  pct_bankroll — fixed percentage of current bankroll (BANKROLL_PCT)
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def compute_stake(
    ev: float,
    edge_pct: float,
    odds_american: int,
    bankroll: float,
) -> float:
    """
    Return stake in dollars based on configured STAKING_MODE.
    Always returns a non-negative value.
    """
    from app.config import (
        BANKROLL_PCT,
        FLAT_STAKE,
        KELLY_FRACTION,
        STAKING_MODE,
    )

    mode = (STAKING_MODE or "flat").lower().strip()

    if mode == "flat":
        stake = float(FLAT_STAKE)

    elif mode == "kelly":
        stake = _kelly_stake(ev, odds_american, bankroll, KELLY_FRACTION)

    elif mode == "pct_bankroll":
        stake = float(bankroll) * float(BANKROLL_PCT)

    else:
        logger.warning("Unknown STAKING_MODE='%s' — falling back to flat", mode)
        stake = float(FLAT_STAKE)

    stake = max(0.0, stake)
    logger.debug(
        "Stake computed | mode=%s ev=%.4f odds=%s bankroll=%.2f → stake=%.2f",
        mode, ev, odds_american, bankroll, stake,
    )
    return stake


def _kelly_stake(ev: float, odds_american: int, bankroll: float, fraction: float) -> float:
    """
    Fractional Kelly criterion.

    Kelly fraction = (b*p - q) / b  where:
      b = decimal profit on a winning $1 bet
      p = estimated win probability (derived from EV)
      q = 1 - p
    """
    try:
        if odds_american > 0:
            b = odds_american / 100.0
        else:
            b = 100.0 / abs(odds_american)

        # Back out win prob from EV:  ev = p*b - q = p*b - (1-p) = p*(b+1) - 1
        # => p = (ev + 1) / (b + 1)
        p = (ev + 1.0) / (b + 1.0)
        p = max(0.0, min(1.0, p))
        q = 1.0 - p

        full_kelly = (b * p - q) / b
        fractional = full_kelly * fraction
        stake = max(0.0, fractional * float(bankroll))
        return stake
    except (ZeroDivisionError, ValueError, OverflowError):
        from app.config import FLAT_STAKE
        logger.warning("Kelly calculation failed — falling back to flat stake")
        return float(FLAT_STAKE)
