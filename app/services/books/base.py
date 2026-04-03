"""
Abstract sportsbook provider interface.

All providers (paper, Cloudbet, DraftKings, etc.) must implement this.
Add new providers by subclassing SportsbookProvider and registering in factory.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class BetRequest:
    game_id: int
    event_id: str
    market_type: str        # moneyline | total | spread
    side: str               # away_ml | home_ml | over | under
    line: Optional[float]   # total line for over/under; None for ML
    odds_american: int
    stake: float
    confidence: str
    edge_pct: float
    ev: float
    source_snapshot_time: Optional[datetime] = None
    source_rank: Optional[int] = None


@dataclass
class QuoteResponse:
    available: bool
    odds_american: int
    line: Optional[float]
    stake_accepted: float
    slippage: float         # abs difference from requested odds as decimal (e.g. 0.01 = 1%)
    message: str = ""


@dataclass
class PlaceBetResponse:
    success: bool
    external_bet_id: Optional[str]
    placed_odds: Optional[int]
    placed_line: Optional[float]
    placed_stake: float
    fill_status: str        # filled | partial | rejected
    raw_response: dict = field(default_factory=dict)
    message: str = ""


@dataclass
class BalanceResponse:
    available: float
    total: float
    currency: str = "USD"


class SportsbookProvider(ABC):
    """Base interface all sportsbook providers must implement."""

    @abstractmethod
    def get_balance(self) -> BalanceResponse: ...

    @abstractmethod
    def find_market(self, bet_request: BetRequest) -> bool: ...

    @abstractmethod
    def get_quote(self, bet_request: BetRequest) -> QuoteResponse: ...

    @abstractmethod
    def place_bet(self, bet_request: BetRequest) -> PlaceBetResponse: ...

    @abstractmethod
    def cancel_bet(self, external_bet_id: str) -> bool: ...

    @abstractmethod
    def get_open_bets(self) -> list[dict]: ...

    @abstractmethod
    def get_settled_bets(self, since: Optional[datetime] = None) -> list[dict]: ...

    def snapshot_bankroll(self, available: float) -> None:
        """Optional: persist a bankroll snapshot. No-op by default."""
