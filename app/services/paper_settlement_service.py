"""Offline score validation and operator backfill for the two paper ledgers."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.schema import Game, GameOutcomeReview

FINAL_STATUSES = {"Final", "Game Over", "Completed Early"}


def final_scores(db: Session, game: Game | None) -> tuple[int, int] | None:
    """Require a final game and consistent, complete scores; never grade live scores."""
    if game is None or (game.status or "").strip() not in FINAL_STATUSES:
        return None
    reviews = {
        (r.final_away_score, r.final_home_score)
        for r in db.query(GameOutcomeReview).filter(GameOutcomeReview.game_id == game.game_id)
    }
    if len(reviews) > 1:
        return None
    scores = (game.final_away_score, game.final_home_score)
    if None in scores:
        if not reviews:
            return None
        fallback = next(iter(reviews))
        if any(s is not None and s != f for s, f in zip(scores, fallback)):
            return None
        scores = fallback
    if reviews and scores not in reviews:
        return None
    if any(s is None or s < 0 for s in scores) or sum(scores) == 0:
        return None
    return scores


def paper_outcome(play: str, away: int, home: int, line) -> str | None:
    if play in {"away_ml", "home_ml"}:
        difference = away - home if play == "away_ml" else home - away
    elif play in {"over", "under"} and line is not None:
        difference = away + home - float(line)
        if play == "under":
            difference = -difference
    else:
        return None
    return "win" if difference > 0 else "loss" if difference < 0 else "push"


def backfill_finished_paper_bets(db: Session) -> dict:
    """Operator entry point: no fetches, placements, provider APIs, or date cutoff.

    Uses the supplied session only. Each ledger is committed atomically and is
    independently idempotent, so retrying after a partial failure is safe.
    Missing execution records are not fabricated. Does not create new trades.
    """
    from app.services.paper_trade_service import settle_finished_paper_trades
    from app.services.execution_service import settle_paper_bets

    return {
        "paper_trades": settle_finished_paper_trades(db),
        "bet_orders": settle_paper_bets(db),
    }
