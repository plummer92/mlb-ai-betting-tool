from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.schema import EdgeResult, Game, GameOdds, GameOutcomeReview, LineMovement, PaperTrade, SnapshotType
from app.services.ev_math import american_to_decimal, implied_prob_raw
from app.services.market_respect_service import (
    classify_tradable_signal,
    has_positive_market_clv,
    market_respect_adjustment,
    market_respect_bucket,
    market_respect_for_edge,
)
from app.services.paper_trade_service import DEFAULT_PAPER_STAKE
from app.services.totals_policy_service import evaluate_totals_policy


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


def get_clv_report(db: Session, *, limit: int = 25) -> dict:
    rows = (
        db.query(PaperTrade, EdgeResult)
        .outerjoin(EdgeResult, EdgeResult.id == PaperTrade.edge_result_id)
        .order_by(PaperTrade.game_date.desc(), PaperTrade.id.desc())
        .limit(limit)
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
        if edge:
            respect = market_respect_for_edge(db, edge)
            row["market_respect_score"] = respect["score"]
            row["market_respect_tags"] = respect["tags"]
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
        "sample_limit": limit,
        "sampled_rows": len(details),
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


def _edge_ev(edge: EdgeResult | None, review: GameOutcomeReview) -> float:
    play = ((edge.recommended_play if edge else None) or review.recommended_play or "").lower()
    if edge is None:
        return 0.0
    if play == "away_ml":
        return float(edge.ev_away or 0)
    if play == "home_ml":
        return float(edge.ev_home or 0)
    if play == "over":
        return float(edge.ev_over or 0)
    if play == "under":
        return float(edge.ev_under or 0)
    return 0.0


def _play_odds(edge: EdgeResult | None, odds: GameOdds | None, review: GameOutcomeReview) -> int | None:
    play = ((edge.recommended_play if edge else None) or review.recommended_play or "").lower()
    if play == "away_ml":
        return edge.away_ml if edge and edge.away_ml is not None else (odds.away_ml if odds else None)
    if play == "home_ml":
        return edge.home_ml if edge and edge.home_ml is not None else (odds.home_ml if odds else None)
    if play == "over":
        return edge.over_odds if edge and edge.over_odds is not None else (odds.over_odds if odds else None)
    if play == "under":
        return edge.under_odds if edge and edge.under_odds is not None else (odds.under_odds if odds else None)
    return None


def _volatility(rows: list[dict]) -> float:
    if len(rows) < 2:
        return 0.0
    mean = sum(row["profit_units"] for row in rows) / len(rows)
    variance = sum((row["profit_units"] - mean) ** 2 for row in rows) / len(rows)
    return round(variance ** 0.5, 4)


def _max_drawdown(rows: list[dict]) -> float:
    peak = 0.0
    cumulative = 0.0
    drawdown = 0.0
    for row in rows:
        cumulative += row["profit_units"]
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
    return round(abs(drawdown), 4)


def _safe_delta(after: float | None, before: float | None) -> float | None:
    if after is None or before is None:
        return None
    return round(after - before, 4)


def _has_positive_clv(row: dict) -> bool:
    return has_positive_market_clv({"components": row.get("market_respect_components") or {}})


def _is_stale_market(row: dict) -> bool:
    tags = row.get("market_respect_tags") or []
    freshness_status = ((row.get("market_respect_components") or {}).get("freshness_status") or "").lower()
    return "STALE OPEN" in tags or freshness_status in {"stale_open", "stale_feed"}


def _is_market_agreed(row: dict) -> bool:
    tags = row.get("market_respect_tags") or []
    bucket = row.get("market_respect_bucket")
    return bucket in {"strong_market_agreement", "market_agreement"} or "MARKET AGREED" in tags


def _is_market_rejected(row: dict) -> bool:
    tags = row.get("market_respect_tags") or []
    return row.get("market_respect_bucket") == "market_rejection" or "MARKET REJECTED" in tags


def _is_market_neutral(row: dict) -> bool:
    tags = row.get("market_respect_tags") or []
    return not _is_market_agreed(row) and not _is_market_rejected(row) and "MARKET NEUTRAL" in tags


def _has_strong_model(row: dict) -> bool:
    confidence = (row.get("adjusted_confidence") or "").lower()
    return confidence == "strong" or abs(float(row.get("raw_edge_pct") or 0)) >= 0.08 or float(row.get("raw_ev") or 0) >= 0.08


def _tradable_signal(row: dict) -> tuple[str, str]:
    signal = classify_tradable_signal(
        market_respect={
            "score": row.get("market_respect_score"),
            "tags": row.get("market_respect_tags"),
            "components": row.get("market_respect_components") or {},
        },
        market_adjustment=row.get("market_respect_adjustment"),
        play=row.get("play"),
        raw_edge_pct=row.get("raw_edge_pct"),
        raw_ev=row.get("raw_ev"),
        adjusted_confidence=row.get("adjusted_confidence"),
    )
    return signal["tradable_signal"], signal["tradable_reason"]


def get_movement_backtest_report(db: Session, *, min_sample: int = 3, limit: int = 50) -> dict:
    rows = (
        db.query(GameOutcomeReview, EdgeResult, GameOdds, LineMovement, Game)
        .outerjoin(EdgeResult, EdgeResult.id == GameOutcomeReview.edge_result_id)
        .outerjoin(GameOdds, GameOdds.id == EdgeResult.odds_id)
        .outerjoin(LineMovement, LineMovement.id == EdgeResult.movement_id)
        .outerjoin(Game, Game.game_id == GameOutcomeReview.game_id)
        .filter(GameOutcomeReview.bet_result.in_(["win", "loss", "push"]))
        .order_by(GameOutcomeReview.game_date.desc(), GameOutcomeReview.id.desc())
        .limit(limit)
        .all()
    )

    normalized = []
    for review, edge, odds, movement, game in rows:
        respect = (
            market_respect_for_edge(
                db,
                edge,
                odds=odds,
                movement=movement,
                game=game,
                evaluate_freshness=False,
            )
            if edge
            else None
        )
        adjustment = market_respect_adjustment(
            edge_pct=float(edge.edge_pct or 0) if edge else review.edge_pct,
            ev=_edge_ev(edge, review),
            confidence=(edge.confidence_tier if edge else None) or review.confidence_tier,
            market_respect=respect,
            odds_american=_play_odds(edge, odds, review),
        ) if respect else None
        totals_policy = (
            evaluate_totals_policy(db, edge=edge, game=game, odds=odds, market_respect=respect)
            if edge and (edge.recommended_play or "").lower() in {"over", "under"}
            else None
        )
        row = {
            "play": (review.recommended_play or "none").lower(),
            "movement_direction": (review.movement_direction or "none").lower(),
            "movement_bucket": _movement_bucket(movement),
            "market_respect_score": respect["score"] if respect else None,
            "market_respect_bucket": market_respect_bucket(respect["score"] if respect else None),
            "market_respect_tags": respect["tags"] if respect else [],
            "market_respect_components": respect.get("components", {}) if respect else {},
            "raw_edge_pct": adjustment["raw_edge_pct"] if adjustment else float(review.edge_pct or 0),
            "adjusted_edge_pct": adjustment["adjusted_edge_pct"] if adjustment else float(review.edge_pct or 0),
            "raw_ev": adjustment["raw_ev"] if adjustment else 0.0,
            "adjusted_ev": adjustment["adjusted_ev"] if adjustment else 0.0,
            "adjusted_confidence": adjustment["adjusted_confidence"] if adjustment else None,
            "adjusted_kelly_fraction": adjustment["adjusted_kelly_fraction"] if adjustment else 0.0,
            "market_respect_adjustment": adjustment,
            "totals_policy_score": totals_policy.get("totals_policy_score") if totals_policy else None,
            "totals_policy_status": totals_policy.get("policy_status") if totals_policy else None,
            "totals_policy_alert_allowed": bool(totals_policy and totals_policy.get("alert_allowed")),
            "after_market_respect_gate": bool(
                adjustment
                and adjustment["alert_allowed"]
                and adjustment["adjusted_edge_pct"] > 0
                and adjustment["adjusted_ev"] > 0
            ),
            "bet_result": review.bet_result,
            "profit_units": _profit_units(review, odds),
        }
        row["tradable_signal"], row["tradable_reason"] = _tradable_signal(row)
        normalized.append(row)

    by_direction: dict[str, list[dict]] = defaultdict(list)
    by_bucket: dict[str, list[dict]] = defaultdict(list)
    by_play_direction: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_play_bucket: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_respect_bucket: dict[str, list[dict]] = defaultdict(list)
    by_respect_tag: dict[str, list[dict]] = defaultdict(list)
    by_tradable_signal: dict[str, list[dict]] = defaultdict(list)
    by_play_tradable_signal: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in normalized:
        by_direction[row["movement_direction"]].append(row)
        by_bucket[row["movement_bucket"]].append(row)
        by_play_direction[(row["play"], row["movement_direction"])].append(row)
        by_play_bucket[(row["play"], row["movement_bucket"])].append(row)
        by_respect_bucket[row["market_respect_bucket"]].append(row)
        by_tradable_signal[row["tradable_signal"]].append(row)
        by_play_tradable_signal[(row["play"], row["tradable_signal"])].append(row)
        for tag in row["market_respect_tags"] or ["UNTAGGED"]:
            by_respect_tag[tag].append(row)

    after_rows = [row for row in normalized if row["after_market_respect_gate"]]
    before_stats = _segment_stats(normalized)
    after_stats = _segment_stats(after_rows)
    before_vol = _volatility(normalized)
    after_vol = _volatility(after_rows)
    before_drawdown = _max_drawdown(normalized)
    after_drawdown = _max_drawdown(after_rows)
    aligned_rows = [
        row for row in normalized
        if row["market_respect_bucket"] in {"strong_market_agreement", "market_agreement"}
        or "MARKET AGREED" in row["market_respect_tags"]
    ]
    rejected_rows = [
        row for row in normalized
        if row["market_respect_bucket"] == "market_rejection"
        or "MARKET REJECTED" in row["market_respect_tags"]
    ]
    positive_clv_rows = [
        row for row in normalized
        if (row["market_respect_components"].get("price_clv") or 0) > 0
        or (row["market_respect_components"].get("line_clv") or 0) > 0
    ]
    sharp_follow_rows = [
        row for row in normalized
        if row["market_respect_components"].get("sharp_match")
    ]
    trade_rows = [row for row in normalized if row["tradable_signal"] == "TRADE"]
    market_respect_only_rows = [
        row for row in normalized
        if _is_market_agreed(row) and not _is_stale_market(row) and float(row.get("adjusted_ev") or 0) > 0
    ]
    totals_policy_market_rows = [
        row for row in trade_rows
        if row["play"] in {"over", "under"} and row["totals_policy_alert_allowed"]
    ]
    rejected_decisions = sum(1 for row in rejected_rows if row["bet_result"] in {"win", "loss"})
    rejected_losses = sum(1 for row in rejected_rows if row["bet_result"] == "loss")

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
        "summary": before_stats,
        "market_respect_weighting_backtest": {
            "before": before_stats,
            "after": after_stats,
            "kept_bets": len(after_rows),
            "filtered_bets": len(normalized) - len(after_rows),
            "roi_delta": _safe_delta(after_stats["roi_per_bet"], before_stats["roi_per_bet"]),
            "win_rate_delta": _safe_delta(after_stats["win_rate"], before_stats["win_rate"]),
            "volatility_before": before_vol,
            "volatility_after": after_vol,
            "volatility_reduction": round(before_vol - after_vol, 4),
            "max_drawdown_before": before_drawdown,
            "max_drawdown_after": after_drawdown,
            "drawdown_reduction": round(before_drawdown - after_drawdown, 4),
        },
        "new_metrics": {
            "market_alignment_roi": _segment_stats(aligned_rows)["roi_per_bet"],
            "market_disagreement_loss_rate": round(rejected_losses / rejected_decisions, 4) if rejected_decisions else None,
            "clv_adjusted_roi": _segment_stats(positive_clv_rows)["roi_per_bet"],
            "sharp_follow_accuracy": _segment_stats(sharp_follow_rows)["win_rate"],
        },
        "market_respect_v2_backtest": {
            "model_only": before_stats,
            "market_respect_only": _segment_stats(market_respect_only_rows),
            "model_plus_market_respect": _segment_stats(trade_rows),
            "totals_policy_plus_market_respect": _segment_stats(totals_policy_market_rows),
        },
        "by_movement_direction": rows_for(by_direction, ("movement_direction",)),
        "by_movement_bucket": rows_for(by_bucket, ("movement_bucket",)),
        "by_play_movement_direction": rows_for(by_play_direction, ("play", "movement_direction")),
        "by_play_movement_bucket": rows_for(by_play_bucket, ("play", "movement_bucket")),
        "by_market_respect_bucket": rows_for(by_respect_bucket, ("market_respect_bucket",)),
        "by_market_respect_tag": rows_for(by_respect_tag, ("market_respect_tag",)),
        "by_tradable_signal": rows_for(by_tradable_signal, ("tradable_signal",)),
        "by_play_tradable_signal": rows_for(by_play_tradable_signal, ("play", "tradable_signal")),
        "min_sample": min_sample,
        "sample_limit": limit,
        "sampled_rows": len(normalized),
    }
