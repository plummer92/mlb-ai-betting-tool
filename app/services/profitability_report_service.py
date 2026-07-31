from __future__ import annotations

import json
from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from app.models.schema import EdgeResult, GameOdds, GameOutcomeReview, TradableDecisionSnapshot
from app.services.betting_policy import BETTING_PROFILES, qualifies_for_bet_policy
from app.services.ev_math import american_to_decimal


SHADOW_POLICY_V3_START_DATE = date(2026, 7, 7)
SHADOW_POLICY_V4_MIN_EDGE = 0.12
SHADOW_POLICY_V5_MIN_EDGE = 0.18
SHADOW_POLICY_V5_MAX_EDGE = 0.25


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


def _shadow_edge_bucket(edge_pct: float | None) -> str:
    edge = float(edge_pct or 0)
    if edge < 0.08:
        return "<8%"
    if edge < 0.12:
        return "8-12%"
    if edge < 0.18:
        return "12-18%"
    if edge < 0.25:
        return "18-25%"
    return "25%+"


def _market_score_bucket(score: float | int | None) -> str:
    if score is None:
        return "unknown"
    value = float(score)
    if value < 40:
        return "rejected"
    if value < 47:
        return "low-neutral"
    if value <= 53:
        return "center-neutral"
    if value <= 60:
        return "high-neutral"
    return "agreed"


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


def _snapshot_payload(snapshot: TradableDecisionSnapshot) -> dict:
    try:
        payload = json.loads(snapshot.snapshot_json or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _text_blob(*values: object) -> str:
    parts: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            parts.extend(str(item) for item in value if item is not None)
        else:
            parts.append(str(value))
    return " ".join(parts).lower()


def _snapshot_market_tags(snapshot: TradableDecisionSnapshot, payload: dict) -> list[str]:
    tags = payload.get("market_respect_tags") or payload.get("market_respect_tag") or []
    if isinstance(tags, str):
        tags = [tags]
    market_respect = payload.get("market_respect") or {}
    if isinstance(market_respect, dict):
        tags.extend(market_respect.get("tags") or [])
    adjustment = payload.get("market_respect_adjustment") or {}
    if isinstance(adjustment, dict):
        tags.extend(adjustment.get("tags") or [])
    if snapshot.market_respect_tag:
        tags.append(snapshot.market_respect_tag)
    return [str(tag).upper() for tag in tags if tag]


def is_shadow_policy_v2_candidate(snapshot: TradableDecisionSnapshot, payload: dict | None = None) -> bool:
    payload = payload if payload is not None else _snapshot_payload(snapshot)
    adjustment = payload.get("market_respect_adjustment") or {}
    if not isinstance(adjustment, dict):
        adjustment = {}
    tags = _snapshot_market_tags(snapshot, payload)
    text = _text_blob(
        snapshot.tradable_reason,
        snapshot.decision_reason,
        snapshot.policy_status,
        payload.get("tradable_reason"),
        payload.get("decision_reason"),
        payload.get("policy_reason"),
        payload.get("policy_reasons"),
        tags,
    )
    confidence = str(
        adjustment.get("adjusted_confidence")
        or adjustment.get("raw_confidence")
        or payload.get("confidence")
        or ""
    ).lower()
    edge = float(snapshot.adjusted_edge_pct or payload.get("adjusted_edge_pct") or payload.get("raw_edge_pct") or 0)
    score = snapshot.market_respect_score
    if score is None:
        score = payload.get("market_trust_score") or adjustment.get("score")
    score_value = float(score) if score is not None else None
    neutral_market = (
        "MARKET NEUTRAL" in tags
        or "market neutral" in text
        or (score_value is not None and 40 <= score_value <= 60 and "MARKET REJECTED" not in tags and "MARKET AGREED" not in tags)
    )
    strong_model = "strong model" in text or confidence == "strong"
    no_positive_clv = "no positive clv" in text or "without_clv" in text
    return bool(
        strong_model
        and neutral_market
        and no_positive_clv
        and edge >= 0.08
        and not snapshot.trade_allowed
        and snapshot.decision_status in {"WATCH", "BLOCKED"}
    )


def is_shadow_policy_v3_candidate(snapshot: TradableDecisionSnapshot, payload: dict | None = None) -> bool:
    if snapshot.game_date is None or snapshot.game_date < SHADOW_POLICY_V3_START_DATE:
        return False
    return (snapshot.play or "").lower() == "under" and is_shadow_policy_v2_candidate(snapshot, payload)


def is_shadow_policy_v4_candidate(snapshot: TradableDecisionSnapshot, payload: dict | None = None) -> bool:
    payload = payload if payload is not None else _snapshot_payload(snapshot)
    edge = float(snapshot.adjusted_edge_pct or payload.get("adjusted_edge_pct") or payload.get("raw_edge_pct") or 0)
    score = snapshot.market_respect_score
    if score is None:
        adjustment = payload.get("market_respect_adjustment") or {}
        if not isinstance(adjustment, dict):
            adjustment = {}
        score = payload.get("market_trust_score") or adjustment.get("score")
    return (
        is_shadow_policy_v3_candidate(snapshot, payload)
        and edge >= SHADOW_POLICY_V4_MIN_EDGE
        and _market_score_bucket(score) == "low-neutral"
    )


def is_shadow_policy_v5_candidate(snapshot: TradableDecisionSnapshot, payload: dict | None = None) -> bool:
    payload = payload if payload is not None else _snapshot_payload(snapshot)
    edge = float(snapshot.adjusted_edge_pct or payload.get("adjusted_edge_pct") or payload.get("raw_edge_pct") or 0)
    return (
        is_shadow_policy_v4_candidate(snapshot, payload)
        and SHADOW_POLICY_V5_MIN_EDGE <= edge < SHADOW_POLICY_V5_MAX_EDGE
    )


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


def _timeline_segment_list(grouped: dict, *, key_names: tuple[str, ...], min_sample: int = 1) -> list[dict]:
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
    return sorted(items, key=lambda item: item.get(key_names[0]) or "")


def _forward_policy_ledger(db: Session) -> dict:
    review_by_edge = {
        review.edge_result_id: review
        for review in db.query(GameOutcomeReview)
        .filter(GameOutcomeReview.edge_result_id.isnot(None))
        .all()
    }
    snapshots = (
        db.query(TradableDecisionSnapshot)
        .order_by(TradableDecisionSnapshot.game_date.asc(), TradableDecisionSnapshot.id.asc())
        .all()
    )

    rows = []
    for snapshot in snapshots:
        try:
            payload = json.loads(snapshot.snapshot_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        policy_qualified = bool(payload.get("policy_qualified"))
        would_have_bet = bool(snapshot.trade_allowed and snapshot.decision_status == "FIRE")
        review = review_by_edge.get(snapshot.edge_result_id)
        result = (review.bet_result if review else None)
        odds = None
        rows.append({
            "game_date": snapshot.game_date,
            "month": snapshot.game_date.strftime("%Y-%m") if snapshot.game_date else "unknown",
            "play": (snapshot.play or "").lower(),
            "decision_status": snapshot.decision_status,
            "tradable_signal": snapshot.tradable_signal,
            "policy_qualified": policy_qualified,
            "would_have_bet": would_have_bet,
            "did_bet": would_have_bet,
            "bet_result": result,
            "profit_units": _profit_units(review, odds) if review and would_have_bet else 0.0,
            "profit_dollars_flat_100": _profit_dollars_flat_100(review, odds) if review and would_have_bet else 0.0,
            "edge_pct": float(snapshot.adjusted_edge_pct) if snapshot.adjusted_edge_pct is not None else None,
            "ev": None,
        })

    qualified_rows = [row for row in rows if row["policy_qualified"]]
    would_rows = [row for row in rows if row["would_have_bet"]]
    graded_would_rows = [row for row in would_rows if row["bet_result"] in {"win", "loss", "push"}]

    by_month: dict[str, list[dict]] = defaultdict(list)
    by_play_month: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in graded_would_rows:
        by_month[row["month"]].append(row)
        by_play_month[(row["play"], row["month"])].append(row)

    recent = rows[-25:]
    return {
        "total_snapshots": len(rows),
        "policy_qualified_snapshots": len(qualified_rows),
        "would_have_bet": len(would_rows),
        "graded_would_have_bet": _segment_stats(graded_would_rows),
        "by_month": _timeline_segment_list(by_month, key_names=("month",)),
        "by_play_month": _timeline_segment_list(by_play_month, key_names=("play", "month")),
        "recent": [
            {
                "game_date": row["game_date"].isoformat() if row["game_date"] else None,
                "play": row["play"],
                "decision_status": row["decision_status"],
                "tradable_signal": row["tradable_signal"],
                "policy_qualified": row["policy_qualified"],
                "would_have_bet": row["would_have_bet"],
                "did_bet": row["did_bet"],
                "result": row["bet_result"],
                "edge_pct": row["edge_pct"],
            }
            for row in recent
        ],
    }


def _shadow_policy_v2_ledger(db: Session) -> dict:
    review_by_edge = {
        review.edge_result_id: review
        for review in db.query(GameOutcomeReview)
        .filter(GameOutcomeReview.edge_result_id.isnot(None))
        .all()
    }
    snapshots = (
        db.query(TradableDecisionSnapshot)
        .order_by(TradableDecisionSnapshot.game_date.asc(), TradableDecisionSnapshot.id.asc())
        .all()
    )
    rows = []
    for snapshot in snapshots:
        payload = _snapshot_payload(snapshot)
        if not is_shadow_policy_v2_candidate(snapshot, payload):
            continue
        review = review_by_edge.get(snapshot.edge_result_id)
        result = review.bet_result if review else None
        adjustment = payload.get("market_respect_adjustment") or {}
        if not isinstance(adjustment, dict):
            adjustment = {}
        rows.append({
            "game_date": snapshot.game_date,
            "month": snapshot.game_date.strftime("%Y-%m") if snapshot.game_date else "unknown",
            "matchup": snapshot.matchup,
            "play": (snapshot.play or "").lower(),
            "decision_status": snapshot.decision_status,
            "tradable_signal": snapshot.tradable_signal,
            "tradable_reason": snapshot.tradable_reason,
            "market_respect_score": snapshot.market_respect_score,
            "market_respect_tag": snapshot.market_respect_tag,
            "bet_result": result,
            "profit_units": _profit_units(review, None) if review else 0.0,
            "profit_dollars_flat_100": _profit_dollars_flat_100(review, None) if review else 0.0,
            "edge_pct": float(snapshot.adjusted_edge_pct) if snapshot.adjusted_edge_pct is not None else None,
            "ev": float(adjustment.get("adjusted_ev")) if adjustment.get("adjusted_ev") is not None else None,
        })

    graded_rows = [row for row in rows if row["bet_result"] in {"win", "loss", "push"}]
    by_month: dict[str, list[dict]] = defaultdict(list)
    by_play_month: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_play: dict[str, list[dict]] = defaultdict(list)
    for row in graded_rows:
        by_month[row["month"]].append(row)
        by_play_month[(row["play"], row["month"])].append(row)
        by_play[row["play"]].append(row)

    recent = rows[-25:]
    return {
        "name": "Shadow Policy V2",
        "description": "Strong model + neutral market + no positive CLV. Shadow-only; never enables real trades.",
        "candidate_snapshots": len(rows),
        "graded_candidates": len(graded_rows),
        "graded": _segment_stats(graded_rows),
        "by_month": _timeline_segment_list(by_month, key_names=("month",)),
        "by_play_month": _timeline_segment_list(by_play_month, key_names=("play", "month")),
        "by_play": _sorted_segment_list(by_play, key_names=("play",), min_sample=1),
        "recent": [
            {
                "game_date": row["game_date"].isoformat() if row["game_date"] else None,
                "matchup": row["matchup"],
                "play": row["play"],
                "decision_status": row["decision_status"],
                "tradable_signal": row["tradable_signal"],
                "tradable_reason": row["tradable_reason"],
                "market_respect_score": row["market_respect_score"],
                "market_respect_tag": row["market_respect_tag"],
                "result": row["bet_result"],
                "edge_pct": row["edge_pct"],
                "ev": row["ev"],
            }
            for row in recent
        ],
    }


def _shadow_policy_v3_ledger(db: Session) -> dict:
    return _shadow_policy_under_ledger(
        db,
        name="Shadow Policy V3",
        description=(
            "Forward/current-policy shadow: UNDER only, strong model, neutral market, no positive CLV, "
            f"tracked from {SHADOW_POLICY_V3_START_DATE.isoformat()}. Shadow-only; never enables real trades."
        ),
        candidate_fn=is_shadow_policy_v3_candidate,
    )


def _shadow_policy_v4_ledger(db: Session) -> dict:
    return _shadow_policy_under_ledger(
        db,
        name="Shadow Policy V4",
        description=(
            "Tighter forward shadow: UNDER only, strong model, low-neutral market score, no positive CLV, "
            f"edge >= {SHADOW_POLICY_V4_MIN_EDGE:.0%}, tracked from {SHADOW_POLICY_V3_START_DATE.isoformat()}. "
            "Shadow-only; never enables real trades."
        ),
        candidate_fn=is_shadow_policy_v4_candidate,
    )


def _shadow_policy_v5_ledger(db: Session) -> dict:
    return _shadow_policy_under_ledger(
        db,
        name="Shadow Policy V5",
        description=(
            "Tighter forward shadow: V4 unders limited to the historically cleaner "
            f"{SHADOW_POLICY_V5_MIN_EDGE:.0%}-{SHADOW_POLICY_V5_MAX_EDGE:.0%} edge bucket. "
            "Shadow-only; never enables real trades."
        ),
        candidate_fn=is_shadow_policy_v5_candidate,
    )


def _shadow_policy_under_ledger(db: Session, *, name: str, description: str, candidate_fn) -> dict:
    review_by_edge = {
        review.edge_result_id: review
        for review in db.query(GameOutcomeReview)
        .filter(GameOutcomeReview.edge_result_id.isnot(None))
        .all()
    }
    snapshots = (
        db.query(TradableDecisionSnapshot)
        .filter(TradableDecisionSnapshot.game_date >= SHADOW_POLICY_V3_START_DATE)
        .order_by(TradableDecisionSnapshot.game_date.asc(), TradableDecisionSnapshot.id.asc())
        .all()
    )
    rows = []
    for snapshot in snapshots:
        payload = _snapshot_payload(snapshot)
        if not candidate_fn(snapshot, payload):
            continue
        review = review_by_edge.get(snapshot.edge_result_id)
        result = review.bet_result if review else None
        adjustment = payload.get("market_respect_adjustment") or {}
        if not isinstance(adjustment, dict):
            adjustment = {}
        edge_pct = float(snapshot.adjusted_edge_pct) if snapshot.adjusted_edge_pct is not None else None
        ev = float(adjustment.get("adjusted_ev")) if adjustment.get("adjusted_ev") is not None else None
        score = snapshot.market_respect_score
        rows.append({
            "game_date": snapshot.game_date,
            "month": snapshot.game_date.strftime("%Y-%m") if snapshot.game_date else "unknown",
            "matchup": snapshot.matchup,
            "play": (snapshot.play or "").lower(),
            "decision_status": snapshot.decision_status,
            "tradable_signal": snapshot.tradable_signal,
            "tradable_reason": snapshot.tradable_reason,
            "market_respect_score": score,
            "market_score_bucket": _market_score_bucket(score),
            "market_respect_tag": snapshot.market_respect_tag,
            "edge_bucket": _shadow_edge_bucket(edge_pct),
            "bet_result": result,
            "profit_units": _profit_units(review, None) if review else 0.0,
            "profit_dollars_flat_100": _profit_dollars_flat_100(review, None) if review else 0.0,
            "edge_pct": edge_pct,
            "ev": ev,
        })

    graded_rows = [row for row in rows if row["bet_result"] in {"win", "loss", "push"}]
    by_month: dict[str, list[dict]] = defaultdict(list)
    by_edge_bucket: dict[str, list[dict]] = defaultdict(list)
    by_score_bucket: dict[str, list[dict]] = defaultdict(list)
    by_status: dict[str, list[dict]] = defaultdict(list)
    by_edge_month: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in graded_rows:
        by_month[row["month"]].append(row)
        by_edge_bucket[row["edge_bucket"]].append(row)
        by_score_bucket[row["market_score_bucket"]].append(row)
        by_status[row["decision_status"]].append(row)
        by_edge_month[(row["edge_bucket"], row["month"])].append(row)

    recent = rows[-25:]
    return {
        "name": name,
        "description": description,
        "start_date": SHADOW_POLICY_V3_START_DATE.isoformat(),
        "min_edge": (
            SHADOW_POLICY_V5_MIN_EDGE if name.endswith("V5")
            else SHADOW_POLICY_V4_MIN_EDGE if name.endswith("V4")
            else None
        ),
        "max_edge": SHADOW_POLICY_V5_MAX_EDGE if name.endswith("V5") else None,
        "candidate_snapshots": len(rows),
        "graded_candidates": len(graded_rows),
        "graded": _segment_stats(graded_rows),
        "by_month": _timeline_segment_list(by_month, key_names=("month",)),
        "by_edge_bucket": _sorted_segment_list(by_edge_bucket, key_names=("edge_bucket",), min_sample=1),
        "by_market_score_bucket": _sorted_segment_list(by_score_bucket, key_names=("market_score_bucket",), min_sample=1),
        "by_decision_status": _sorted_segment_list(by_status, key_names=("decision_status",), min_sample=1),
        "by_edge_bucket_month": _timeline_segment_list(by_edge_month, key_names=("edge_bucket", "month")),
        "recent": [
            {
                "game_date": row["game_date"].isoformat() if row["game_date"] else None,
                "matchup": row["matchup"],
                "play": row["play"],
                "decision_status": row["decision_status"],
                "tradable_signal": row["tradable_signal"],
                "tradable_reason": row["tradable_reason"],
                "market_respect_score": row["market_respect_score"],
                "market_score_bucket": row["market_score_bucket"],
                "market_respect_tag": row["market_respect_tag"],
                "edge_bucket": row["edge_bucket"],
                "result": row["bet_result"],
                "edge_pct": row["edge_pct"],
                "ev": row["ev"],
            }
            for row in recent
        ],
    }


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
                "season": review.game_date.year if review.game_date else None,
                "month": review.game_date.strftime("%Y-%m") if review.game_date else "unknown",
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
    by_season: dict[int, list[dict]] = defaultdict(list)
    by_month: dict[str, list[dict]] = defaultdict(list)
    by_play_month: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for row in normalized_rows:
        by_play[row["play"]].append(row)
        by_confidence[row["confidence"]].append(row)
        by_edge_bucket[row["edge_bucket"]].append(row)
        by_play_edge[(row["play"], row["edge_bucket"])].append(row)
        by_play_conf[(row["play"], row["confidence"])].append(row)
        if row["season"] is not None:
            by_season[row["season"]].append(row)
        by_month[row["month"]].append(row)
        by_play_month[(row["play"], row["month"])].append(row)

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
        "by_season": _timeline_segment_list(by_season, key_names=("season",), min_sample=min_sample),
        "by_month": _timeline_segment_list(by_month, key_names=("month",), min_sample=min_sample),
        "by_play_month": _timeline_segment_list(by_play_month, key_names=("play", "month"), min_sample=min_sample),
        "forward_policy_ledger": _forward_policy_ledger(db),
        "shadow_policy_v2": _shadow_policy_v2_ledger(db),
        "shadow_policy_v3": _shadow_policy_v3_ledger(db),
        "shadow_policy_v4": _shadow_policy_v4_ledger(db),
        "shadow_policy_v5": _shadow_policy_v5_ledger(db),
        "insights": insights,
    }
