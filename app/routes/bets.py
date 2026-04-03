"""
Betting execution API routes.

Paper mode is always available.
Live endpoints are gated behind BETTING_ENABLED=true + BETTING_MODE=live.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.execution_service import (
    create_candidate_bets_for_today,
    execute_paper_bets_for_today,
    get_execution_summary,
    settle_paper_bets,
)
from app.services.kill_switch import (
    activate_kill_switch,
    deactivate_kill_switch,
    get_status as ks_status,
)

router = APIRouter(prefix="/api/bets", tags=["bets"])


# ── Read endpoints ────────────────────────────────────────────────────────────

@router.get("/candidates")
def list_candidates(db: Session = Depends(get_db)):
    """
    Return today's elite bet candidates (EV ≥ 0.10 · Edge ≥ 0.10 · Strong).
    Read-only — no DB writes.
    """
    candidates = create_candidate_bets_for_today(db)
    return {
        "count": len(candidates),
        "thresholds": {"min_ev": 0.10, "min_edge": 0.10, "confidence": "strong"},
        "candidates": candidates,
    }


@router.get("/open")
def list_open_bets(db: Session = Depends(get_db)):
    """Return all open paper bets (placed_paper status)."""
    from app.services.books.factory import get_provider
    provider = get_provider(db)
    return {"open_bets": provider.get_open_bets()}


@router.get("/settled")
def list_settled_bets(db: Session = Depends(get_db)):
    """Return all settled paper bets."""
    from app.services.books.factory import get_provider
    provider = get_provider(db)
    return {"settled_bets": provider.get_settled_bets()}


@router.get("/summary")
def execution_summary(db: Session = Depends(get_db)):
    """
    Full execution summary: bankroll, open bets, P/L today + all-time,
    recent settled, rejected bets with reasons.
    """
    return get_execution_summary(db)


# ── Paper execution ───────────────────────────────────────────────────────────

@router.post("/execute-paper")
def execute_paper(db: Session = Depends(get_db)):
    """
    Run the paper execution pipeline for today's elite bets.

    - Fetches candidates from existing edge/ranked data (read-only)
    - Applies risk controls
    - Writes bet_orders + bet_executions records
    - Debits paper bankroll snapshot
    - Idempotent: skips games that already have an active order
    """
    result = execute_paper_bets_for_today(db)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/settle-paper")
def settle_paper(db: Session = Depends(get_db)):
    """
    Settle open paper bets using final game scores already in the DB.
    Safe to call repeatedly — only settles games with final scores.
    """
    return settle_paper_bets(db)


# ── Kill switch ───────────────────────────────────────────────────────────────

@router.post("/kill-switch/on")
def enable_kill_switch():
    """
    Activate the kill switch — blocks all bet execution immediately.
    This is a runtime toggle; does not modify .env.
    """
    activate_kill_switch()
    return ks_status()


@router.post("/kill-switch/off")
def disable_kill_switch():
    """
    Deactivate the kill switch — re-enables bet execution.
    This is a runtime toggle; does not modify .env.
    """
    deactivate_kill_switch()
    return ks_status()


@router.get("/kill-switch")
def kill_switch_status():
    """Return current kill switch state."""
    return ks_status()


# ── Live execution (disabled unless BETTING_MODE=live + BETTING_ENABLED) ──────

@router.post("/execute-live")
def execute_live(db: Session = Depends(get_db)):
    """
    Live bet execution — DISABLED by default.
    Requires BETTING_ENABLED=true, BETTING_MODE=live, and a non-paper provider.
    """
    from app.config import BETTING_ENABLED, BETTING_MODE, BOOK_PROVIDER

    if not BETTING_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Live betting requires BETTING_ENABLED=true in .env",
        )
    if BETTING_MODE != "live":
        raise HTTPException(
            status_code=403,
            detail=f"BETTING_MODE={BETTING_MODE} — set BETTING_MODE=live to enable",
        )
    if (BOOK_PROVIDER or "paper").lower() == "paper":
        raise HTTPException(
            status_code=403,
            detail="BOOK_PROVIDER=paper cannot place live bets. Configure a real provider.",
        )

    raise HTTPException(
        status_code=501,
        detail="Live provider not yet implemented. See app/services/books/cloudbet.py.",
    )
