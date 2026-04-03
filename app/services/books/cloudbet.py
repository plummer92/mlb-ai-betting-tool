"""
Cloudbet live sportsbook provider — SCAFFOLD ONLY.

Not implemented. Plug in Cloudbet API credentials and implement each method
when ready to go live. The factory will return this class when BOOK_PROVIDER=cloudbet.

See https://www.cloudbet.com/api/ for API documentation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.services.books.base import (
    BalanceResponse,
    BetRequest,
    PlaceBetResponse,
    QuoteResponse,
    SportsbookProvider,
)

_NOT_ENABLED = "Live sportsbook provider not enabled — set BOOK_PROVIDER=paper or implement CloudbetProvider"


class CloudbetProvider(SportsbookProvider):
    """
    Cloudbet live provider scaffold.

    To implement:
    1. Set CLOUDBET_API_KEY in .env
    2. Implement each method below using the Cloudbet REST API
    3. Set BOOK_PROVIDER=cloudbet and BETTING_MODE=live in .env
    4. Set BETTING_ENABLED=true ONLY after thorough paper-mode testing
    """

    def __init__(self) -> None:
        import os
        self._api_key = os.getenv("CLOUDBET_API_KEY", "")
        self._base_url = os.getenv("CLOUDBET_API_URL", "https://sports-api.cloudbet.com/pub/v2")

    def get_balance(self) -> BalanceResponse:
        raise NotImplementedError(_NOT_ENABLED)

    def find_market(self, bet_request: BetRequest) -> bool:
        raise NotImplementedError(_NOT_ENABLED)

    def get_quote(self, bet_request: BetRequest) -> QuoteResponse:
        raise NotImplementedError(_NOT_ENABLED)

    def place_bet(self, bet_request: BetRequest) -> PlaceBetResponse:
        raise NotImplementedError(_NOT_ENABLED)

    def cancel_bet(self, external_bet_id: str) -> bool:
        raise NotImplementedError(_NOT_ENABLED)

    def get_open_bets(self) -> list[dict]:
        raise NotImplementedError(_NOT_ENABLED)

    def get_settled_bets(self, since: Optional[datetime] = None) -> list[dict]:
        raise NotImplementedError(_NOT_ENABLED)
