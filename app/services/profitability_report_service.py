from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.schema import EdgeResult, GameOdds, GameOutcomeReview
from app.services.betting_policy import BETTING_PROFILES, qualifies_for_bet_policy
from app.services.ev_math import american_to_decimal


def _edge_bucket(edge_pct: float | None) -> str:
    edge = float(edge_pct or 0)
    if edge < 0.05:
        return "<5%"
    if edge < 0.10:
        return "5-10%"
    if edge < 0.15:
        return "10-15%"
    if edge < 0.20:
        return "15-20%"
    return "20%+"


def _american_odds(review: GameOutcomeReview, odds: GameOdds | None) -> int | None:
    if odds is None:
        return None
    play = (review.recommended_play or "").lower()
    if play == "away_ml":
        return odds.away_ml
    if play == "home_ml":
        return odds.home_ml
    if play == "over":
        return odds.over_odds
    if play == "under":
        return odds.under_odds
    return None


def _edge_ev(review: GameOutcomeReview, edge: EdgeResult | None) -> float | None:
    if review.ev is not None:
        return float(review.ev)
    if edge is None:
        return None
    play = (review.recommended_play or "").lower()
    if play == "away_ml":
        return float(edge.ev_away) if edge.ev_away is not None else None
    if play == "home_ml":
        return float(edge.ev_home) if edge.ev_home is not None else None
    if play == "over":
        return float(edge.ev_over) if edge.ev_over is not None else None
    if play == "under":
        return float(edge.ev_under) if edge.ev_under is not None else None
    return None


def _profit_units(review: GameOutcomeReview, odds: GameOdds | None) -> float:
    result = (review.bet_result or "").lower()
    if result == "push":
        return 0.0
    if result == "loss":
        return -1.0
    if result != "win":
        return 0.0

    american = _american_odds(review, odds)
    if american is None:
        return 100 / 110
    return american_to_decimal(american) - 1.0


def _profit_dollars_flat_100(review: GameOutcomeReview, odds: GameOdds | None) -> float:
    return round(_profit_units(review, odds) * 100.0, 2)


def _segment_stats(rows: list[dict]) -> dict:
    total = len(rows)
    wins = sum(1 for row in rows if row["bet_result"] == "win")
    losses = sum(1 for row in rows if row["bet_result"] == "loss")
    pushes = sum(1 for row in rows if row["bet_result"] == "push")
    decisions = wins + losses
    roi_units_total = round(sum(row["profit_units"] for row in rows), 4)
    roi_per_bet = round(roi_units_total / total, 4) if total else 0.0
    avg_edge = round(sum(row["edge_pct"] for row in rows if row["edge_pct"] is not None) / max(sum(1 for row in rows if row["edge_pct"] is not None), 1), 4)
    avg_ev = round(sum(row["ev"] for row in rows if row["ev"] is not None) / max(sum(1 for row in rows if row["ev"] is not None), 1), 4)
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate": round(wins / decisions, 4) if decisions else None,
        "roi_units_total": roi_units_total,
        "roi_per_bet": roi_per_bet,
        "profit_flat_100": round(sum(row["profit_dollars_flat_100"] for row in rows), 2),
        "avg_edge": avg_edge,
        "avg_ev": avg_ev,
    }


def _sorted_segment_list(grouped: dict, *, key_names: tuple[str, ...], min_sample: int) -> list[dict]:
    items = []
    for key, rows in grouped.items():
        if len(rows) < min_sample:
            continue
        row = _segment_stats(rows)
        if len(key_names) == 1:
            row[key_names[0]] = key
        else:
            for idx, key_name in enumerate(key_names):
                row[key_name] = key[idx]
        items.append(row)
    return sorted(items, key=lambda item: (item["roi_per_bet"], item["win_rate"] or 0, item["total"]), reverse=True)


def get_profitability_report(db: Session, *, min_sample: int = 5) -> dict:
    pairs = (
        db.query(GameOutcomeReview, EdgeResult, GameOdds)
        .outerjoin(EdgeResult, EdgeResult.id == GameOutcomeReview.edge_result_id)
        .outerjoin(GameOdds, GameOdds.id == EdgeResult.odds_id)
        .filter(GameOutcomeReview.bet_result.in_(["win", "loss", "push"]))
        .all()
    )

    normalized_rows = []
    for review, edge, odds in pairs:
        play = (review.recommended_play or "").lower()
        confidence = (review.confidence_tier or "none").lower()
        edge_pct = float(review.edge_pct or 0) if review.edge_pct is not None else None
        ev = _edge_ev(review, edge)
        normalized_rows.append(
            {
                "play": play,
                "confidence": confidence,
                "edge_bucket": _edge_bucket(edge_pct),
                "bet_result": review.bet_result,
                "edge_pct": edge_pct,
                "ev": ev,
                "profit_units": _profit_units(review, odds),
                "profit_dollars_flat_100": _profit_dollars_flat_100(review, odds),
                "policy_qualified": qualifies_for_bet_policy(
                    play=play,
                    edge_pct=edge_pct,
                    ev=ev,
                    confidence=confidence,
                ),
            }
        )

    by_play: dict[str, list[dict]] = defaultdict(list)
    by_confidence: dict[str, list[dict]] = defaultdict(list)
    by_edge_bucket: dict[str, list[dict]] = defaultdict(list)
    by_play_edge: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_play_conf: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for row in normalized_rows:
        by_play[row["play"]].append(row)
        by_confidence[row["confidence"]].append(row)
        by_edge_bucket[row["edge_bucket"]].append(row)
        by_play_edge[(row["play"], row["edge_bucket"])].append(row)
        by_play_conf[(row["play"], row["confidence"])].append(row)

    all_stats = _segment_stats(normalized_rows) if normalized_rows else _segment_stats([])
    policy_rows = [row for row in normalized_rows if row["policy_qualified"]]
    policy_stats = _segment_stats(policy_rows) if policy_rows else _segment_stats([])

    insights = []
    play_segments = _sorted_segment_list(by_play, key_names=("play",), min_sample=min_sample)
    if play_segments:
        insights.append(f"Best market so far: {play_segments[0]['play']} ({play_segments[0]['roi_per_bet']:.4f} units/bet).")
        insights.append(f"Worst market so far: {play_segments[-1]['play']} ({play_segments[-1]['roi_per_bet']:.4f} units/bet).")

    play_edge_segments = _sorted_segment_list(by_play_edge, key_names=("play", "edge_bucket"), min_sample=min_sample)
    if play_edge_segments:
        best = play_edge_segments[0]
        worst = play_edge_segments[-1]
        insights.append(
            f"Strongest edge bucket: {best['play']} in {best['edge_bucket']} ({best['roi_per_bet']:.4f} units/bet over {best['total']} bets)."
        )
        insights.append(
            f"Most dangerous bucket: {worst['play']} in {worst['edge_bucket']} ({worst['roi_per_bet']:.4f} units/bet over {worst['total']} bets)."
        )

    return {
        "summary": all_stats,
        "policy_backtest": {
            "current_tightened_policy": policy_stats,
            "profiles": BETTING_PROFILES,
        },
        "by_play": play_segments,
        "by_confidence": _sorted_segment_list(by_confidence, key_names=("confidence",), min_sample=min_sample),
        "by_edge_bucket": _sorted_segment_list(by_edge_bucket, key_names=("edge_bucket",), min_sample=min_sample),
        "by_play_edge_bucket": play_edge_segments,
        "by_play_confidence": _sorted_segment_list(by_play_conf, key_names=("play", "confidence"), min_sample=min_sample),
        "insights": insights,
    }
