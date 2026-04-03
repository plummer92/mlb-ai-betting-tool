"""
Provider factory — returns the correct SportsbookProvider based on config.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.books.base import SportsbookProvider


def get_provider(db: Session) -> SportsbookProvider:
    """
    Return the active sportsbook provider.

    Reads BOOK_PROVIDER from config:
      paper    → PaperSportsbookProvider (default, safe, no real money)
      cloudbet → CloudbetProvider (scaffold — raises NotImplementedError)
    """
    from app.config import BOOK_PROVIDER, DEFAULT_BANKROLL

    key = (BOOK_PROVIDER or "paper").lower().strip()

    if key == "paper":
        from app.services.books.paper import PaperSportsbookProvider
        return PaperSportsbookProvider(db=db, initial_bankroll=DEFAULT_BANKROLL)

    if key == "cloudbet":
        from app.services.books.cloudbet import CloudbetProvider
        return CloudbetProvider()

    raise ValueError(
        f"Unknown BOOK_PROVIDER='{key}'. "
        "Valid options: paper, cloudbet"
    )
