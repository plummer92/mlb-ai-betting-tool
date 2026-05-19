from __future__ import annotations

from collections import Counter, defaultdict
from statistics import pstdev

from sqlalchemy.orm import Session

from app.models.schema import EdgeResult, Game, GameOdds, GameOutcomeReview, LineMovement, SandboxPredictionV4
from app.services.ev_math import american_to_decimal


TOTAL_PLAYS = {"over", "under"}


def _to_float(value, default: float | None = None) -> float | None:
    if value is None:
        return default
    return float(value)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _latest_sandbox(db: Session, game_id: int | None) -> SandboxPredictionV4 | None:
    if game_id is None:
        return None
    return (
        db.query(SandboxPredictionV4)
        .filter(SandboxPredictionV4.game_id == game_id)
        .order_by(SandboxPredictionV4.created_at.desc(), SandboxPredictionV4.id.desc())
        .first()
    )


def _line_clv_for_play(play: str, market_respect: dict | None) -> float | None:
    components = (market_respect or {}).get("components") or {}
    value = components.get("line_clv")
    return float(value) if value is not None else None


def _price_clv_for_play(market_respect: dict | None) -> float | None:
    components = (market_respect or {}).get("components") or {}
    value = components.get("price_clv")
    return float(value) if value is not None else None


def _movement_persistence(play: str, market_respect: dict | None) -> tuple[int, list[str]]:
    components = (market_respect or {}).get("components") or {}
    tags = market_respect.get("tags", []) if market_respect else []
    points = 8
    reasons: list[str] = []
    direction = (components.get("movement_direction") or "").lower()
    line_move = components.get("line_move")
    sharp_match = bool(components.get("sharp_match"))
    sharp_against = bool(components.get("sharp_against"))
    became_more_expensive = bool(components.get("became_more_expensive"))
    late_move = bool(components.get("late_move"))

    if direction == "toward_model":
        points += 8
        reasons.append("line moved toward model")
    elif direction == "away_from_model":
        points -= 12
        reasons.append("line moved away from model")
    if line_move is not None and abs(float(line_move)) >= 0.5 and play in TOTAL_PLAYS:
        points += 6
        reasons.append("persistent total move")
    if sharp_match or "LATE SHARP BUY" in tags:
        points += 10
        reasons.append("sharp signal matched play")
    if sharp_against:
        points -= 16
        reasons.append("sharp signal opposed play")
    if late_move and became_more_expensive:
        points += 5
        reasons.append("late price pressure")
    return int(_clamp(points, 0, 25)), reasons


def _historical_bucket_points(delta_abs: float, play: str) -> tuple[int, str]:
    # Seeded from /api/debug/totals-bias research. Keep deliberately modest:
    # this is a prior, not a guarantee.
    if delta_abs <= 0.25:
        return -25, "near-market totals have poor historical ROI"
    if delta_abs < 1.0:
        return 0 if play == "under" else -8, "weak total disagreement bucket"
    if delta_abs < 2.0:
        return -4 if play == "under" else 2, "medium disagreement bucket"
    return 10 if play == "under" else 8, "large disagreement bucket has the best prior"


def _explosive_environment(
    *,
    play: str,
    prediction,
    sandbox: SandboxPredictionV4 | None,
    game: Game | None,
) -> tuple[int, list[str]]:
    if play != "under":
        return 0, []

    penalty = 0
    reasons: list[str] = []
    wind_factor = _to_float(getattr(sandbox, "wind_factor", None))
    if wind_factor is not None:
        if wind_factor >= 0.45:
            penalty += 22
            reasons.append("strong wind out")
        elif wind_factor >= 0.30:
            penalty += 12
            reasons.append("wind out")

    home_bp = _to_float(getattr(sandbox, "home_bullpen_strength", None))
    away_bp = _to_float(getattr(sandbox, "away_bullpen_strength", None))
    if home_bp is not None and away_bp is not None:
        avg_bp = (home_bp + away_bp) / 2
        if avg_bp <= 0.35:
            penalty += 18
            reasons.append("both bullpens weak")
        elif min(home_bp, away_bp) <= 0.35:
            penalty += 10
            reasons.append("one bullpen weak")

    park_factor = _to_float(getattr(prediction, "park_factor_adv", None))
    if park_factor is not None:
        if park_factor >= 0.06:
            penalty += 14
            reasons.append("high park factor")
        elif park_factor >= 0.03:
            penalty += 8
            reasons.append("elevated park factor")

    away_pitcher = (getattr(game, "away_probable_pitcher", None) or "").lower() if game else ""
    home_pitcher = (getattr(game, "home_probable_pitcher", None) or "").lower() if game else ""
    if "opener" in away_pitcher or "opener" in home_pitcher or away_pitcher == "tbd" or home_pitcher == "tbd":
        penalty += 10
        reasons.append("opener or bullpen game risk")

    if sandbox and sandbox.is_series_finale:
        penalty += 6
        reasons.append("series finale bullpen volatility")

    return penalty, reasons


def evaluate_totals_policy(
    db: Session,
    *,
    edge: EdgeResult | None,
    game: Game | None = None,
    prediction=None,
    odds: GameOdds | None = None,
    market_respect: dict | None = None,
) -> dict:
    play = ((edge.recommended_play if edge else None) or "").lower()
    if play not in TOTAL_PLAYS:
        return {
            "totals_policy_score": 100,
            "policy_score": 100,
            "policy_status": "APPROVED",
            "policy_reason": "Non-total market; totals policy not applied.",
            "policy_reasons": [],
            "delta": None,
            "delta_abs": None,
            "clv_positive": None,
            "explosive_environment_penalty": 0,
            "components": {},
            "alert_allowed": True,
        }

    model_total = _to_float(edge.model_total if edge and edge.model_total is not None else getattr(prediction, "projected_total", None))
    market_total = _to_float(edge.book_total if edge and edge.book_total is not None else (odds.total_line if odds and odds.total_line is not None else None))
    if model_total is None or market_total is None:
        return {
            "totals_policy_score": 0,
            "policy_score": 0,
            "policy_status": "BLOCKED",
            "policy_reason": "Missing model or market total.",
            "policy_reasons": ["missing_total_inputs"],
            "delta": None,
            "delta_abs": None,
            "clv_positive": False,
            "explosive_environment_penalty": 0,
            "components": {},
            "alert_allowed": False,
        }

    delta = round(model_total - market_total, 3)
    delta_abs = abs(delta)
    score = 35
    reasons: list[str] = []
    components: dict[str, float | int | str | bool | None] = {
        "model_total": model_total,
        "market_total": market_total,
        "delta": delta,
        "delta_abs": round(delta_abs, 3),
    }

    if delta_abs <= 0.25:
        return {
            "totals_policy_score": 0,
            "policy_score": 0,
            "policy_status": "BLOCKED",
            "policy_reason": "Blocked: model is too close to market total, where historical ROI is poor.",
            "policy_reasons": ["near_market_noise"],
            "delta": delta,
            "delta_abs": round(delta_abs, 3),
            "clv_positive": False,
            "explosive_environment_penalty": 0,
            "components": {**components, "edge_band": "near_market"},
            "alert_allowed": False,
        }

    if delta_abs < 1.0:
        score += 6
        edge_band = "weak"
        reasons.append("weak_total_edge")
    elif delta_abs < 2.0:
        score += 22
        edge_band = "medium"
        reasons.append("medium_total_edge")
    else:
        score += 36
        edge_band = "extreme"
        reasons.append("extreme_total_edge")

    respect_score = int((market_respect or {}).get("score", 50))
    tags = list((market_respect or {}).get("tags") or [])
    rejected = "MARKET REJECTED" in tags or respect_score < 40
    score += round((respect_score - 50) * 0.35)
    if respect_score >= 75:
        reasons.append("strong_market_agreement")
    elif respect_score >= 60:
        reasons.append("market_agreement")
    elif respect_score < 40:
        reasons.append("market_rejection")

    line_clv = _line_clv_for_play(play, market_respect)
    price_clv = _price_clv_for_play(market_respect)
    clv_positive = (line_clv is not None and line_clv > 0) or (price_clv is not None and price_clv > 0)
    if clv_positive:
        score += 10
        reasons.append("positive_clv")
    elif line_clv is not None or price_clv is not None:
        score -= 8
        reasons.append("no_positive_clv")

    movement_points, movement_reasons = _movement_persistence(play, market_respect)
    score += movement_points - 8
    reasons.extend(movement_reasons)

    bucket_points, bucket_reason = _historical_bucket_points(delta_abs, play)
    score += bucket_points
    reasons.append(bucket_reason)

    if play == "under":
        score -= 6
        reasons.append("broad_under_bias_penalty")
    elif play == "over":
        score -= 10
        reasons.append("recommended_overs_have_weak_history")

    sandbox = _latest_sandbox(db, edge.game_id if edge else None)
    explosive_penalty, explosive_reasons = _explosive_environment(
        play=play,
        prediction=prediction,
        sandbox=sandbox,
        game=game,
    )
    score -= explosive_penalty
    reasons.extend(explosive_reasons)

    status = "APPROVED"
    gate_reasons: list[str] = []
    if rejected and respect_score < 35:
        status = "BLOCKED"
        gate_reasons.append("severe_market_rejection")
    elif edge_band == "weak" and respect_score < 75:
        status = "BLOCKED"
        gate_reasons.append("weak_edge_requires_strong_market_agreement")
    elif edge_band == "medium" and (respect_score < 60 or not clv_positive or rejected):
        status = "BLOCKED"
        gate_reasons.append("medium_edge_requires_market_respect_and_positive_clv")
    elif explosive_penalty >= 22:
        status = "BLOCKED"
        gate_reasons.append("explosive_run_environment")
    elif score < 45:
        status = "CAUTION"
    elif explosive_penalty > 0 or edge_band == "weak":
        status = "CAUTION"

    all_reasons = list(dict.fromkeys(gate_reasons + reasons))
    final_score = int(_clamp(score, 0, 100))
    if status == "BLOCKED":
        final_score = min(final_score, 39)
    elif status == "CAUTION":
        final_score = min(final_score, 69)

    components.update({
        "edge_band": edge_band,
        "market_respect_score": respect_score,
        "line_clv": line_clv,
        "price_clv": price_clv,
        "movement_points": movement_points,
        "historical_bucket_points": bucket_points,
        "explosive_environment_penalty": explosive_penalty,
        "market_respect_tags": tags,
        "sandbox_available": sandbox is not None,
    })
    return {
        "totals_policy_score": final_score,
        "policy_score": final_score,
        "policy_status": status,
        "policy_reason": _policy_reason(status, all_reasons, final_score),
        "policy_reasons": all_reasons,
        "delta": delta,
        "delta_abs": round(delta_abs, 3),
        "clv_positive": clv_positive,
        "explosive_environment_penalty": explosive_penalty,
        "components": components,
        "alert_allowed": status in {"APPROVED", "CAUTION"},
    }


def _policy_reason(status: str, reasons: list[str], score: int) -> str:
    readable = ", ".join(reason.replace("_", " ") for reason in reasons[:4])
    if status == "BLOCKED":
        return f"Blocked by totals policy ({score}/100): {readable}."
    if status == "CAUTION":
        return f"Caution from totals policy ({score}/100): {readable}."
    if status == "CLUSTER_RISK":
        return f"Cluster risk from totals policy ({score}/100): {readable}."
    return f"Approved by totals policy ({score}/100): {readable}."


def apply_under_cluster_risk(rows: list[dict], *, threshold: float = 0.70) -> dict:
    totals_rows = [row for row in rows if (row.get("play") or "").lower() in TOTAL_PLAYS]
    if not totals_rows:
        return {"under_share": 0.0, "warning": None, "penalized": 0}
    under_rows = [row for row in totals_rows if (row.get("play") or "").lower() == "under"]
    under_share = len(under_rows) / len(totals_rows)
    warning = "UNDER CLUSTER RISK" if under_share > threshold else None
    penalized = 0
    if warning:
        for row in under_rows:
            policy = dict(row.get("totals_policy") or {})
            if policy.get("policy_status") == "BLOCKED":
                continue
            old_score = int(policy.get("totals_policy_score", 50))
            new_score = max(0, old_score - 12)
            policy["totals_policy_score"] = new_score
            policy["policy_score"] = new_score
            policy["cluster_risk"] = True
            policy["cluster_under_share"] = round(under_share, 4)
            reasons = list(policy.get("policy_reasons") or [])
            if "UNDER CLUSTER RISK" not in reasons:
                reasons.insert(0, "UNDER CLUSTER RISK")
            policy["policy_reasons"] = reasons
            if policy.get("policy_status") == "APPROVED":
                policy["policy_status"] = "CLUSTER_RISK"
            policy["policy_reason"] = _policy_reason(policy["policy_status"], reasons, new_score)
            policy["alert_allowed"] = policy["policy_status"] in {"APPROVED", "CAUTION"}
            row["totals_policy"] = policy
            row["totals_policy_score"] = new_score
            row["policy_status"] = policy["policy_status"]
            row["policy_reason"] = policy["policy_reason"]
            row["policy_reasons"] = reasons
            row["totals_policy_alert_allowed"] = policy["alert_allowed"]
            penalized += 1
    return {"under_share": round(under_share, 4), "warning": warning, "penalized": penalized}


def _profit_units(result: str, odds_american: int | None = None) -> float:
    result = (result or "").lower()
    if result == "push":
        return 0.0
    if result == "loss":
        return -1.0
    if result != "win":
        return 0.0
    if odds_american is None:
        return 100 / 110
    return american_to_decimal(int(odds_american)) - 1.0


def _volatility(rows: list[dict]) -> float:
    if len(rows) < 2:
        return 0.0
    return round(pstdev([row["profit_units"] for row in rows]), 4)


def _max_drawdown(rows: list[dict]) -> float:
    peak = 0.0
    cumulative = 0.0
    drawdown = 0.0
    for row in rows:
        cumulative += row["profit_units"]
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
    return round(abs(drawdown), 4)


def _segment_stats(rows: list[dict]) -> dict:
    wins = sum(1 for row in rows if row["result"] == "win")
    losses = sum(1 for row in rows if row["result"] == "loss")
    pushes = sum(1 for row in rows if row["result"] == "push")
    decisions = wins + losses
    clv_rows = [row for row in rows if row.get("line_clv") is not None]
    profit = round(sum(row["profit_units"] for row in rows), 4)
    return {
        "bets": len(rows),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": round(wins / decisions, 4) if decisions else None,
        "roi": round(profit / len(rows), 4) if rows else 0.0,
        "profit_units": profit,
        "volatility": _volatility(rows),
        "drawdown": _max_drawdown(rows),
        "avg_line_clv": round(sum(row["line_clv"] for row in clv_rows) / len(clv_rows), 3) if clv_rows else None,
    }


def _odds_for_review_play(review: GameOutcomeReview, edge: EdgeResult | None, odds: GameOdds | None) -> int | None:
    play = (review.recommended_play or "").lower()
    if play == "over":
        return edge.over_odds if edge and edge.over_odds is not None else (odds.over_odds if odds else None)
    if play == "under":
        return edge.under_odds if edge and edge.under_odds is not None else (odds.under_odds if odds else None)
    return None


def totals_policy_backtest(db: Session, *, min_sample: int = 5) -> dict:
    rows = (
        db.query(GameOutcomeReview, EdgeResult, GameOdds, LineMovement, Game)
        .join(EdgeResult, EdgeResult.id == GameOutcomeReview.edge_result_id)
        .outerjoin(GameOdds, GameOdds.id == EdgeResult.odds_id)
        .outerjoin(LineMovement, LineMovement.id == EdgeResult.movement_id)
        .outerjoin(Game, Game.game_id == GameOutcomeReview.game_id)
        .filter(
            GameOutcomeReview.recommended_play.in_(["over", "under"]),
            GameOutcomeReview.bet_result.in_(["win", "loss", "push"]),
        )
        .order_by(GameOutcomeReview.game_date.asc(), GameOutcomeReview.id.asc())
        .all()
    )

    normalized: list[dict] = []
    by_policy_status: dict[str, list[dict]] = defaultdict(list)
    reason_counts: Counter[str] = Counter()
    for review, edge, odds, movement, game in rows:
        market_respect = {
            "score": 50,
            "tags": [],
            "components": {
                "line_clv": None,
                "price_clv": None,
                "movement_direction": review.movement_direction,
                "line_move": float(movement.total_move or 0) if movement else None,
                "sharp_match": bool(
                    movement
                    and (
                        (review.recommended_play == "over" and movement.total_steam_over)
                        or (review.recommended_play == "under" and movement.total_steam_under)
                    )
                ),
                "sharp_against": bool(
                    movement
                    and (
                        (review.recommended_play == "over" and movement.total_steam_under)
                        or (review.recommended_play == "under" and movement.total_steam_over)
                    )
                ),
            },
        }
        if movement and movement.pregame_total is not None and review.book_total is not None:
            book_total = float(review.book_total)
            pregame_total = float(movement.pregame_total)
            if review.recommended_play == "over":
                market_respect["components"]["line_clv"] = round(pregame_total - book_total, 3)
            elif review.recommended_play == "under":
                market_respect["components"]["line_clv"] = round(book_total - pregame_total, 3)
        policy = evaluate_totals_policy(db, edge=edge, game=game, prediction=None, odds=odds, market_respect=market_respect)
        row = {
            "game_id": review.game_id,
            "play": review.recommended_play,
            "result": review.bet_result,
            "profit_units": _profit_units(review.bet_result, _odds_for_review_play(review, edge, odds)),
            "line_clv": policy.get("components", {}).get("line_clv"),
            "policy_status": policy["policy_status"],
            "totals_policy_score": policy["totals_policy_score"],
            "market_respect_score": market_respect["score"],
            "policy_reasons": policy["policy_reasons"],
        }
        normalized.append(row)
        by_policy_status[row["policy_status"]].append(row)
        for reason in row["policy_reasons"]:
            reason_counts[reason] += 1

    policy_filtered = [row for row in normalized if row["policy_status"] in {"APPROVED", "CAUTION", "CLUSTER_RISK"}]
    policy_market = [
        row
        for row in policy_filtered
        if row["market_respect_score"] >= 60
        or row.get("line_clv") is not None and row["line_clv"] > 0
    ]
    return {
        "raw_totals_model": _segment_stats(normalized),
        "totals_policy_filtered": _segment_stats(policy_filtered),
        "totals_policy_plus_market_respect": _segment_stats(policy_market),
        "by_policy_status": {
            status: stats
            for status, stats in (
                (status, _segment_stats(items))
                for status, items in sorted(by_policy_status.items())
                if len(items) >= min_sample
            )
        },
        "filter_reason_counts": [{"reason": reason, "count": count} for reason, count in reason_counts.most_common(10)],
    }
