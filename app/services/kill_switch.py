"""
Runtime kill switch for bet execution.

State is held in memory and initialized from KILL_SWITCH env var.
Toggle via POST /api/bets/kill-switch/on|off.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Initialized lazily from config on first read
_state: bool | None = None


def _load_initial() -> bool:
    from app.config import KILL_SWITCH
    return KILL_SWITCH


def is_kill_switch_active() -> bool:
    global _state
    if _state is None:
        _state = _load_initial()
    return _state


def activate_kill_switch() -> None:
    global _state
    _state = True
    logger.critical("KILL SWITCH ACTIVATED — all bet execution blocked")


def deactivate_kill_switch() -> None:
    global _state
    _state = False
    logger.warning("Kill switch deactivated — bet execution re-enabled")


def get_status() -> dict:
    return {
        "kill_switch_active": is_kill_switch_active(),
        "message": (
            "All bet execution is BLOCKED"
            if is_kill_switch_active()
            else "Bet execution is enabled"
        ),
    }
