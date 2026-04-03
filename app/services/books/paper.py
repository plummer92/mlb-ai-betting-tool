"""
Paper (simulated) sportsbook provider.

Executes bets locally with no external API calls.
Quotes use the odds already stored in the DB.
Settlement is driven by actual game scores in the games table.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.betting import BankrollSnapshot, BetOrder
from app.services.books.base import (
    BalanceResponse,
    BetRequest,
    PlaceBetResponse,
    QuoteResponse,
    SportsbookProvider,
)

logger = logging.getLogger(__name__)


class PaperSportsbookProvider(SportsbookProvider):
    """Paper-mode provider — simulates betting with no real money."""

    MODE = "paper"
    SPORTSBOOK = "paper"

    def __init__(self, db: Session, initial_bankroll: float = 1000.0):
        self._db = db
        self._initial_bankroll = initial_bankroll

    # ── Balance ──────────────────────────────────────────────────────────

    def _load_current_balance(self) -> float:
        snap = (
            self._db.query(BankrollSnapshot)
            .filter(BankrollSnapshot.provider_mode == self.MODE)
            .order_by(BankrollSnapshot.captured_at.desc())
            .first()
        )
        return float(snap.available_balance) if snap else self._initial_bankroll

    def get_balance(self) -> BalanceResponse:
        bal = self._load_current_balance()
        return BalanceResponse(available=bal, total=bal, currency="USD")

    def snapshot_bankroll(self, available: float) -> None:
        snap = BankrollSnapshot(
            provider_mode=self.MODE,
            sportsbook=self.SPORTSBOOK,
            bankroll=available,
            available_balance=available,
            captured_at=datetime.now(timezone.utc),
        )
        self._db.add(snap)
        self._db.commit()

    # ── Market / Quote ────────────────────────────────────────────────────

    def find_market(self, bet_request: BetRequest) -> bool:
        return True  # paper always has every market

    def get_quote(self, bet_request: BetRequest) -> QuoteResponse:
        # Paper quotes never move the line
        return QuoteResponse(
            available=True,
            odds_american=bet_request.odds_american,
            line=bet_request.line,
            stake_accepted=bet_request.stake,
            slippage=0.0,
            message="paper quote — no slippage applied",
        )

    # ── Execution ─────────────────────────────────────────────────────────

    def place_bet(self, bet_request: BetRequest) -> PlaceBetResponse:
        external_id = f"paper-{uuid.uuid4().hex[:12]}"
        logger.info(
            "PAPER BET PLACED | game=%s side=%s stake=%.2f odds=%s ev=%.4f edge=%.4f",
            bet_request.game_id,
            bet_request.side,
            bet_request.stake,
            bet_request.odds_american,
            bet_request.ev,
            bet_request.edge_pct,
        )
        return PlaceBetResponse(
            success=True,
            external_bet_id=external_id,
            placed_odds=bet_request.odds_american,
            placed_line=bet_request.line,
            placed_stake=bet_request.stake,
            fill_status="filled",
            raw_response={"mode": "paper", "id": external_id},
            message="paper bet accepted",
        )

    def cancel_bet(self, external_bet_id: str) -> bool:
        logger.info("PAPER CANCEL: %s", external_bet_id)
        return True

    # ── Open / Settled queries ────────────────────────────────────────────

    def get_open_bets(self) -> list[dict]:
        orders = (
            self._db.query(BetOrder)
            .filter(
                BetOrder.provider_mode == self.MODE,
                BetOrder.status == "placed_paper",
            )
            .all()
        )
        return [_order_dict(o) for o in orders]

    def get_settled_bets(self, since: Optional[datetime] = None) -> list[dict]:
        q = self._db.query(BetOrder).filter(
            BetOrder.provider_mode == self.MODE,
            BetOrder.status == "settled",
        )
        if since:
            q = q.filter(BetOrder.created_at >= since)
        return [_order_dict(o) for o in q.all()]


def _order_dict(o: BetOrder) -> dict:
    return {
        "id": o.id,
        "game_id": o.game_id,
        "side": o.side,
        "market_type": o.market_type,
        "stake": float(o.requested_stake or 0),
        "odds": o.requested_odds,
        "ev": float(o.ev or 0),
        "edge_pct": float(o.edge_pct or 0),
        "confidence": o.confidence,
        "status": o.status,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }
