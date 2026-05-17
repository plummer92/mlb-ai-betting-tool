from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.schema import EdgeResult, GameOdds, GameOutcomeReview, LineMovement, PaperTrade, SnapshotType
from app.services.ev_math import american_to_decimal, implied_prob_raw
from app.services.paper_trade_service import DEFAULT_PAPER_STAKE


FINAL_SNAPSHOT = SnapshotType.pregame


def _closing_odds_for_trade(db: Session, trade: PaperTrade, edge: EdgeResult | None) -> GameOdds | None:
    sportsbook = edge.sportsbook if edge and edge.sportsbook else None
    query = db.query(GameOdds).filter(
        GameOdds.game_id == trade.game_id,
        GameOdds.snapshot_type == FINAL_SNAPSHOT,
    )
    if sportsbook:
        same_book = (
            query.filter(GameOdds.sportsbook == sportsbook)
            .order_by(GameOdds.fetched_at.desc(), GameOdds.id.desc())
            .first()
        )
        if same_book:
            return same_book
    return query.order_by(GameOdds.fetched_at.desc(), GameOdds.id.desc()).first()


def _closing_price_for_play(odds: GameOdds | None, play: str) -> tuple[int | None, float | None]:
    if odds is None:
        return None, None
    if play == "away_ml":
        return odds.away_ml, None
    if play == "home_ml":
        return odds.home_ml, None
    if play == "over":
        return odds.over_odds, float(odds.total_line) if odds.total_line is not None else None
    if play == "under":
        return odds.under_odds, float(odds.total_line) if odds.total_line is not None else None
    return None, None


def _signed_clv(trade: PaperTrade, close_odds: int | None, close_line: float | None) -> dict:
    play = (trade.play or "").lower()
    entry_odds = int(trade.odds) if trade.odds is not None else None
    entry_line = float(trade.line) if trade.line is not None else None
    price_clv = None
    line_clv = None

    if entry_odds is not None and close_odds is not None:
        price_clv = round(implied_prob_raw(close_odds) - implied_prob_raw(entry_odds), 4)

    if play == "over" and entry_line is not None and close_line is not None:
        line_clv = round(close_line - entry_line, 2)
    elif play == "under" and entry_line is not None and close_line is not None:
        line_clv = round(entry_line - close_line, 2)

    return {
        "entry_odds": entry_odds,
        "closing_odds": close_odds,
        "entry_line": entry_line,
        "closing_line": close_line,
        "price_clv": price_clv,
        "line_clv": line_clv,
        "beat_close": (price_clv is not None and price_clv > 0) or (line_clv is not None and line_clv > 0),
    }


def get_clv_report(db: Session) -> dict:
    rows = (
        db.query(PaperTrade, EdgeResult)
        .outerjoin(EdgeResult, EdgeResult.id == PaperTrade.edge_result_id)
        .order_by(PaperTrade.game_date.desc(), PaperTrade.id.desc())
        .all()
    )

    details = []
    by_play: dict[str, list[dict]] = defaultdict(list)
    for trade, edge in rows:
        close = _closing_odds_for_trade(db, trade, edge)
        close_odds, close_line = _closing_price_for_play(close, (trade.play or "").lower())
        clv = _signed_clv(trade, close_odds, close_line)
        row = {
            "trade_id": trade.id,
            "game_id": trade.game_id,
            "game_date": trade.game_date.isoformat(),
            "play": trade.play,
            "confidence": trade.confidence,
            "sportsbook": edge.sportsbook if edge else None,
            "closing_sportsbook": close.sportsbook if close else None,
            **clv,
        }
        details.append(row)
        by_play[trade.play or "unknown"].append(row)

    def summarize(items: list[dict]) -> dict:
        priced = [item for item in items if item["price_clv"] is not None]
        lined = [item for item in items if item["line_clv"] is not None]
        beat = [item for item in items if item["beat_close"]]
        return {
            "bets": len(items),
            "priced": len(priced),
            "lined": len(lined),
            "beat_close": len(beat),
            "beat_close_rate": round(len(beat) / len(items), 4) if items else 0.0,
            "avg_price_clv": round(sum(item["price_clv"] for item in priced) / len(priced), 4) if priced else None,
            "avg_line_clv": round(sum(item["line_clv"] for item in lined) / len(lined), 3) if lined else None,
        }

    return {
        "summary": summarize(details),
        "by_play": [
            {"play": play, **summarize(items)}
            for play, items in sorted(by_play.items())
        ],
        "recent": details[:25],
    }


def _profit_units(review: GameOutcomeReview, odds: GameOdds | None) -> float:
    result = (review.bet_result or "").lower()
    if result == "push":
        return 0.0
    if result == "loss":
        return -1.0
    if result != "win":
        return 0.0
    play = (review.recommended_play or "").lower()
    american = None
    if odds is not None:
        if play == "away_ml":
            american = odds.away_ml
        elif play == "home_ml":
            american = odds.home_ml
        elif play == "over":
            american = odds.over_odds
        elif play == "under":
            american = odds.under_odds
    return american_to_decimal(american) - 1.0 if american is not None else 100 / 110


def _segment_stats(rows: list[dict]) -> dict:
    wins = sum(1 for row in rows if row["bet_result"] == "win")
    losses = sum(1 for row in rows if row["bet_result"] == "loss")
    pushes = sum(1 for row in rows if row["bet_result"] == "push")
    decisions = wins + losses
    profit_units = round(sum(row["profit_units"] for row in rows), 4)
    return {
        "bets": len(rows),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate": round(wins / decisions, 4) if decisions else None,
        "profit_units": profit_units,
        "roi_per_bet": round(profit_units / len(rows), 4) if rows else 0.0,
        "profit_flat_100": round(profit_units * float(DEFAULT_PAPER_STAKE), 2),
    }


def _movement_bucket(movement: LineMovement | None) -> str:
    if movement is None:
        return "no_movement"
    away = abs(float(movement.away_prob_move or 0))
    home = abs(float(movement.home_prob_move or 0))
    total = abs(float(movement.total_move or 0))
    if max(away, home) >= 0.04:
        return "ml_steam"
    if total >= 0.5:
        return "total_steam"
    if max(away, home) >= 0.02 or total >= 0.2:
        return "minor_move"
    return "flat"


def get_movement_backtest_report(db: Session, *, min_sample: int = 3) -> dict:
    rows = (
        db.query(GameOutcomeReview, EdgeResult, GameOdds, LineMovement)
        .outerjoin(EdgeResult, EdgeResult.id == GameOutcomeReview.edge_result_id)
        .outerjoin(GameOdds, GameOdds.id == EdgeResult.odds_id)
        .outerjoin(LineMovement, LineMovement.id == EdgeResult.movement_id)
        .filter(GameOutcomeReview.bet_result.in_(["win", "loss", "push"]))
        .all()
    )

    normalized = []
    for review, edge, odds, movement in rows:
        normalized.append(
            {
                "play": (review.recommended_play or "none").lower(),
                "movement_direction": (review.movement_direction or "none").lower(),
                "movement_bucket": _movement_bucket(movement),
                "bet_result": review.bet_result,
                "profit_units": _profit_units(review, odds),
            }
        )

    by_direction: dict[str, list[dict]] = defaultdict(list)
    by_bucket: dict[str, list[dict]] = defaultdict(list)
    by_play_direction: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_play_bucket: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in normalized:
        by_direction[row["movement_direction"]].append(row)
        by_bucket[row["movement_bucket"]].append(row)
        by_play_direction[(row["play"], row["movement_direction"])].append(row)
        by_play_bucket[(row["play"], row["movement_bucket"])].append(row)

    def rows_for(grouped: dict, key_names: tuple[str, ...]) -> list[dict]:
        output = []
        for key, items in grouped.items():
            if len(items) < min_sample:
                continue
            stats = _segment_stats(items)
            if len(key_names) == 1:
                stats[key_names[0]] = key
            else:
                for idx, key_name in enumerate(key_names):
                    stats[key_name] = key[idx]
            output.append(stats)
        return sorted(output, key=lambda item: (item["roi_per_bet"], item["win_rate"] or 0), reverse=True)

    return {
        "summary": _segment_stats(normalized),
        "by_movement_direction": rows_for(by_direction, ("movement_direction",)),
        "by_movement_bucket": rows_for(by_bucket, ("movement_bucket",)),
        "by_play_movement_direction": rows_for(by_play_direction, ("play", "movement_direction")),
        "by_play_movement_bucket": rows_for(by_play_bucket, ("play", "movement_bucket")),
        "min_sample": min_sample,
    }
