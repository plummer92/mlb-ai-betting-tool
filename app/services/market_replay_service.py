from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.schema import (
    EdgeResult,
    Game,
    GameOdds,
    GameOutcomeReview,
    LineMovement,
    Prediction,
    SharpMoveJournal,
    TradableDecisionSnapshot,
)


def _number(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _matchup(game: Game) -> str:
    return f"{game.away_team} @ {game.home_team}"


def _odds_row(row: GameOdds) -> dict[str, Any]:
    return {
        "id": row.id,
        "source": row.sportsbook,
        "snapshot_type": row.snapshot_type.value if hasattr(row.snapshot_type, "value") else str(row.snapshot_type),
        "fetched_at": _iso(row.fetched_at),
        "away_ml": row.away_ml,
        "home_ml": row.home_ml,
        "total_line": _number(row.total_line),
        "over_odds": row.over_odds,
        "under_odds": row.under_odds,
    }


def _source_summary(rows: list[GameOdds]) -> list[dict[str, Any]]:
    grouped: dict[str, list[GameOdds]] = defaultdict(list)
    for row in rows:
        grouped[row.sportsbook].append(row)
    return [
        {
            "source": source,
            "snapshots": len(source_rows),
            "snapshot_types": sorted({row.snapshot_type.value if hasattr(row.snapshot_type, "value") else str(row.snapshot_type) for row in source_rows}),
            "first_seen_at": _iso(source_rows[0].fetched_at),
            "last_seen_at": _iso(source_rows[-1].fetched_at),
        }
        for source, source_rows in sorted(grouped.items())
    ]


def _journal_row(row: SharpMoveJournal) -> dict[str, Any]:
    return {
        "play": row.play,
        "source": row.sportsbook,
        "captured_at": _iso(row.captured_at),
        "movement_bucket": row.movement_bucket,
        "market_signal": row.market_signal,
        "line_move": _number(row.line_move),
        "price_move": _number(row.price_move),
        "line_clv": _number(row.line_clv),
        "price_clv": _number(row.price_clv),
        "readiness": row.readiness,
    }


def build_market_replay(db: Session, game_id: int) -> dict[str, Any] | None:
    """Return an audit-only record of a completed game and every stored line snapshot."""
    game = db.get(Game, game_id)
    if game is None:
        return None

    odds = (
        db.query(GameOdds)
        .filter(GameOdds.game_id == game_id)
        .order_by(GameOdds.fetched_at.asc(), GameOdds.id.asc())
        .all()
    )
    prediction = (
        db.query(Prediction)
        .filter(Prediction.game_id == game_id)
        .order_by(Prediction.created_at.desc(), Prediction.prediction_id.desc())
        .first()
    )
    edge = (
        db.query(EdgeResult)
        .filter(EdgeResult.game_id == game_id)
        .order_by(EdgeResult.calculated_at.desc(), EdgeResult.id.desc())
        .first()
    )
    review = (
        db.query(GameOutcomeReview)
        .filter(GameOutcomeReview.game_id == game_id)
        .order_by(GameOutcomeReview.created_at.desc(), GameOutcomeReview.id.desc())
        .first()
    )
    decision = (
        db.query(TradableDecisionSnapshot)
        .filter(TradableDecisionSnapshot.game_id == game_id)
        .order_by(TradableDecisionSnapshot.captured_at.desc(), TradableDecisionSnapshot.id.desc())
        .first()
    )
    movement = db.query(LineMovement).filter(LineMovement.game_id == game_id).first()
    journal = (
        db.query(SharpMoveJournal)
        .filter(SharpMoveJournal.game_id == game_id)
        .order_by(SharpMoveJournal.captured_at.asc(), SharpMoveJournal.id.asc())
        .all()
    )
    decision_payload = None
    if decision and decision.snapshot_json:
        try:
            decision_payload = json.loads(decision.snapshot_json)
        except json.JSONDecodeError:
            decision_payload = None

    final_away = review.final_away_score if review else game.final_away_score
    final_home = review.final_home_score if review else game.final_home_score
    return {
        "status": "ok",
        "game": {
            "game_id": game.game_id,
            "game_date": game.game_date.isoformat(),
            "matchup": _matchup(game),
            "away_team": game.away_team,
            "home_team": game.home_team,
            "start_time": game.start_time,
            "status": game.status,
        },
        "outcome": {
            "final_away_score": final_away,
            "final_home_score": final_home,
            "final_score": f"{final_away}-{final_home}" if final_away is not None and final_home is not None else None,
            "winning_side": review.winning_side if review else None,
            "bet_result": review.bet_result if review else None,
            "model_correct": review.was_model_correct if review else None,
            "summary": review.actual_outcome_summary if review else None,
        },
        "model": {
            "version": prediction.model_version if prediction else None,
            "created_at": _iso(prediction.created_at) if prediction else None,
            "away_win_pct": prediction.away_win_pct if prediction else None,
            "home_win_pct": prediction.home_win_pct if prediction else None,
            "projected_total": prediction.projected_total if prediction else None,
            "recommended_side": prediction.recommended_side if prediction else None,
        },
        "edge": {
            "calculated_at": _iso(edge.calculated_at) if edge else None,
            "recommended_play": edge.recommended_play if edge else None,
            "edge_pct": _number(edge.edge_pct) if edge else None,
            "ev_over": _number(edge.ev_over) if edge else None,
            "ev_under": _number(edge.ev_under) if edge else None,
            "sportsbook": edge.sportsbook if edge else None,
            "snapshot_type": edge.odds_snapshot_type if edge else None,
        },
        "decision": {
            "captured_at": _iso(decision.captured_at) if decision else None,
            "status": decision.decision_status if decision else None,
            "signal": decision.tradable_signal if decision else None,
            "trade_allowed": decision.trade_allowed if decision else False,
            "reason": decision.decision_reason if decision else None,
            "policy_status": decision.policy_status if decision else None,
            "policy_score": decision.policy_score if decision else None,
            "raw": decision_payload,
        },
        "movement": {
            "source": movement.sportsbook if movement else None,
            "calculated_at": _iso(movement.calculated_at) if movement else None,
            "total_move": _number(movement.total_move) if movement else None,
            "away_probability_move": _number(movement.away_prob_move) if movement else None,
            "home_probability_move": _number(movement.home_prob_move) if movement else None,
        },
        "odds_timeline": [_odds_row(row) for row in odds],
        "source_summary": _source_summary(odds),
        "sharp_move_journal": [_journal_row(row) for row in journal],
        "guardrail": "Market Replay is an after-the-fact research audit. It does not create or change betting decisions.",
    }


def list_market_replays(db: Session, *, limit: int = 12) -> dict[str, Any]:
    requested_limit = max(1, min(limit, 50))
    games = (
        db.query(Game)
        .filter(Game.final_away_score.is_not(None), Game.final_home_score.is_not(None))
        .order_by(Game.game_date.desc(), Game.game_id.desc())
        .limit(max(requested_limit * 8, 50))
        .all()
    )
    game_ids = [game.game_id for game in games]
    odds_counts = {
        game_id: count
        for game_id, count in (
            db.query(GameOdds.game_id, func.count(GameOdds.id))
            .filter(GameOdds.game_id.in_(game_ids))
            .group_by(GameOdds.game_id)
            .all()
            if game_ids else []
        )
    }
    source_counts = {
        game_id: count
        for game_id, count in (
            db.query(GameOdds.game_id, func.count(func.distinct(GameOdds.sportsbook)))
            .filter(GameOdds.game_id.in_(game_ids))
            .group_by(GameOdds.game_id)
            .all()
            if game_ids else []
        )
    }
    games.sort(key=lambda game: (odds_counts.get(game.game_id, 0) > 0, game.game_date, game.game_id), reverse=True)
    selected_games = games[:requested_limit]
    selected_ids = [game.game_id for game in selected_games]
    latest_reviews = {}
    if selected_ids:
        ranked_reviews = (
            db.query(
                GameOutcomeReview.id.label("review_id"),
                func.row_number().over(
                    partition_by=GameOutcomeReview.game_id,
                    order_by=(GameOutcomeReview.created_at.desc(), GameOutcomeReview.id.desc()),
                ).label("rank"),
            )
            .filter(GameOutcomeReview.game_id.in_(selected_ids))
            .subquery()
        )
        latest_reviews = {
            review.game_id: review
            for review in db.query(GameOutcomeReview)
            .join(ranked_reviews, GameOutcomeReview.id == ranked_reviews.c.review_id)
            .filter(ranked_reviews.c.rank == 1)
            .all()
        }
    rows = []
    for game in selected_games:
        review = latest_reviews.get(game.game_id)
        rows.append({
            "game_id": game.game_id,
            "game_date": game.game_date.isoformat(),
            "matchup": _matchup(game),
            "final_score": f"{game.final_away_score}-{game.final_home_score}",
            "recommended_play": review.recommended_play if review else None,
            "bet_result": review.bet_result if review else None,
            "odds_snapshots": odds_counts.get(game.game_id, 0),
            "sources": source_counts.get(game.game_id, 0),
        })
    return {"status": "ok", "count": len(rows), "rows": rows}
