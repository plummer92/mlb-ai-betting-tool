from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import pstdev

from sqlalchemy.orm import Session

from app.models.schema import EdgeResult, Game, GameOdds, LineMovement, SnapshotType
from app.services.betting_policy import qualifies_for_bet_policy
from app.services.ev_math import american_to_decimal, implied_prob_raw, kelly_fraction
from app.services.odds_service import odds_freshness_metadata


PRODUCTIVE_TOTAL_PLAYS = {"over", "under"}
ML_PLAYS = {"away_ml", "home_ml"}


@dataclass(frozen=True)
class MarketRespect:
    score: int
    tags: list[str]
    components: dict[str, float | int | bool | str | None]

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "tags": self.tags,
            "components": self.components,
        }


def market_respect_bucket(score: int | None) -> str:
    if score is None:
        return "unknown"
    if score >= 75:
        return "strong_market_agreement"
    if score >= 60:
        return "market_agreement"
    if score >= 41:
        return "mixed_market"
    return "market_rejection"


def market_respect_adjustment(
    *,
    edge_pct: float | None,
    ev: float | None,
    confidence: str | None,
    market_respect: dict | None,
    odds_american: int | None = None,
) -> dict:
    """Turn market respect into an actionable calibration layer.

    The raw model edge remains visible, but live ranking, alerting, and risk
    controls can use these adjusted values so market confirmation acts like a
    probabilistic prior instead of a decorative label.
    """
    raw_edge = float(edge_pct or 0.0)
    raw_ev = float(ev or 0.0)
    respect = market_respect or {"score": 50, "tags": ["MARKET NEUTRAL"], "components": {}}
    tags = list(respect.get("tags") or [])
    score = int(respect.get("score", 50))
    score_bucket = market_respect_bucket(score)
    stale = "STALE OPEN" in tags
    rejected = "MARKET REJECTED" in tags or score_bucket == "market_rejection"
    effective_bucket = "stale_open" if stale else score_bucket

    profile = {
        "strong_market_agreement": (1.25, 1.18, 1.25),
        "market_agreement": (1.12, 1.08, 1.10),
        "mixed_market": (1.00, 1.00, 1.00),
        "market_rejection": (0.35, 0.40, 0.00),
        "stale_open": (0.70, 0.75, 0.00),
        "unknown": (0.85, 0.85, 0.50),
    }.get(effective_bucket, (1.0, 1.0, 1.0))

    edge_multiplier, ev_multiplier, kelly_multiplier = profile
    adjusted_edge = round(raw_edge * edge_multiplier, 4)
    adjusted_ev = round(raw_ev * ev_multiplier, 4)
    adjusted_confidence = _adjust_confidence(confidence, effective_bucket, score)
    adjusted_kelly = _adjusted_kelly_fraction(adjusted_ev, odds_american, kelly_multiplier)

    suppress_reasons: list[str] = []
    if stale:
        suppress_reasons.append("stale odds: refresh required before alerting")
    if rejected:
        suppress_reasons.append("market rejected the model side")
    if adjusted_edge <= 0:
        suppress_reasons.append("adjusted edge is not positive")
    if adjusted_ev <= 0:
        suppress_reasons.append("adjusted EV is not positive")

    alert_allowed = not suppress_reasons
    return {
        "bucket": effective_bucket,
        "score_bucket": score_bucket,
        "score": score,
        "tags": tags,
        "raw_edge_pct": round(raw_edge, 4),
        "adjusted_edge_pct": adjusted_edge,
        "raw_ev": round(raw_ev, 4),
        "adjusted_ev": adjusted_ev,
        "raw_confidence": confidence,
        "adjusted_confidence": adjusted_confidence,
        "adjusted_kelly_fraction": adjusted_kelly,
        "edge_multiplier": edge_multiplier,
        "ev_multiplier": ev_multiplier,
        "kelly_multiplier": kelly_multiplier,
        "alert_allowed": alert_allowed,
        "suppress_alert": not alert_allowed,
        "suppress_reasons": suppress_reasons,
        "explanation": _adjustment_explanation(
            effective_bucket,
            edge_multiplier=edge_multiplier,
            ev_multiplier=ev_multiplier,
            adjusted_confidence=adjusted_confidence,
            raw_confidence=confidence,
            alert_allowed=alert_allowed,
            suppress_reasons=suppress_reasons,
        ),
    }


def has_positive_market_clv(market_respect: dict | None) -> bool:
    components = (market_respect or {}).get("components") or {}
    return (components.get("price_clv") or 0) > 0 or (components.get("line_clv") or 0) > 0


def classify_tradable_signal(
    *,
    market_respect: dict | None,
    market_adjustment: dict | None,
    play: str | None = None,
    raw_edge_pct: float | None = None,
    raw_ev: float | None = None,
    adjusted_confidence: str | None = None,
    confidence_score: float | None = None,
) -> dict:
    respect = market_respect or {"score": 50, "tags": ["MARKET NEUTRAL"], "components": {}}
    adjustment = market_adjustment or {}
    tags = list(respect.get("tags") or adjustment.get("tags") or [])
    components = respect.get("components") or {}
    score = int(respect.get("score", adjustment.get("score", 50)))
    bucket = adjustment.get("score_bucket") or market_respect_bucket(score)
    effective_bucket = adjustment.get("bucket") or bucket
    freshness_status = (components.get("freshness_status") or "").lower()

    stale = "STALE OPEN" in tags or freshness_status in {"stale_open", "stale_feed"}
    rejected = "MARKET REJECTED" in tags or bucket == "market_rejection" or effective_bucket == "market_rejection"
    agreed = bucket in {"strong_market_agreement", "market_agreement"} or "MARKET AGREED" in tags
    neutral = not agreed and not rejected and "MARKET NEUTRAL" in tags
    positive_clv = has_positive_market_clv(respect)
    positive_ev = float(adjustment.get("adjusted_ev") or 0) > 0
    confidence = (adjusted_confidence or adjustment.get("adjusted_confidence") or "").lower()
    policy_qualified = qualifies_for_bet_policy(
        play=play,
        edge_pct=adjustment.get("adjusted_edge_pct", raw_edge_pct),
        ev=adjustment.get("adjusted_ev", raw_ev),
        confidence=confidence,
        confidence_score=confidence_score,
    )
    strong_model = (
        confidence == "strong"
        or abs(float(raw_edge_pct if raw_edge_pct is not None else adjustment.get("raw_edge_pct") or 0)) >= 0.08
        or float(raw_ev if raw_ev is not None else adjustment.get("raw_ev") or 0) >= 0.08
    )

    if stale:
        signal, reason = "PASS", "stale market context"
    elif rejected:
        signal, reason = "PASS", "market rejected model side"
    elif agreed and positive_ev and positive_clv and policy_qualified:
        signal, reason = "TRADE", "market agreed with positive CLV and adjusted EV"
    elif agreed and positive_ev and positive_clv:
        signal, reason = "PASS", "outside tightened profitable policy"
    elif neutral and strong_model and positive_ev:
        signal = "WATCH"
        reason = "market neutral with strong model"
        if not positive_clv:
            reason += "; no positive CLV, research only"
    elif not positive_clv:
        signal, reason = "PASS", "no positive CLV"
    elif not positive_ev:
        signal, reason = "PASS", "adjusted EV is not positive"
    else:
        signal, reason = "PASS", "market confirmation insufficient"

    return {
        "tradable_signal": signal,
        "tradable_reason": reason,
        "trade_allowed": signal == "TRADE",
        "positive_clv": positive_clv,
        "positive_adjusted_ev": positive_ev,
        "market_agreed": agreed,
        "market_neutral": neutral,
        "market_rejected": rejected,
        "stale": stale,
        "strong_model": strong_model,
        "policy_qualified": policy_qualified,
    }


def _adjust_confidence(confidence: str | None, bucket: str, score: int) -> str | None:
    levels = [None, "weak", "medium", "strong"]
    current = (confidence or "").lower().strip() or None
    idx = levels.index(current) if current in levels else 0
    if bucket == "strong_market_agreement" and score >= 82:
        idx += 1
    elif bucket == "market_agreement" and idx == 1:
        idx += 1
    elif bucket == "market_rejection":
        idx -= 2
    elif bucket == "stale_open":
        idx -= 1
    idx = max(0, min(idx, len(levels) - 1))
    return levels[idx]


def _adjusted_kelly_fraction(adjusted_ev: float, odds_american: int | None, multiplier: float) -> float:
    if adjusted_ev <= 0 or multiplier <= 0:
        return 0.0
    if odds_american is None:
        return round(min(0.05, adjusted_ev * 0.25 * multiplier), 4)
    decimal_odds = american_to_decimal(int(odds_american))
    if decimal_odds <= 1:
        return 0.0
    model_prob = max(0.0, min(1.0, (adjusted_ev + 1) / decimal_odds))
    return round(min(0.08, kelly_fraction(model_prob, decimal_odds) * multiplier), 4)


def _adjustment_explanation(
    bucket: str,
    *,
    edge_multiplier: float,
    ev_multiplier: float,
    adjusted_confidence: str | None,
    raw_confidence: str | None,
    alert_allowed: bool,
    suppress_reasons: list[str],
) -> str:
    label = {
        "strong_market_agreement": "Market strongly agreed with the model side",
        "market_agreement": "Market modestly agreed with the model side",
        "mixed_market": "Market gave a neutral read",
        "market_rejection": "Market moved against or rejected the model side",
        "stale_open": "Odds are stale",
        "unknown": "Market read is incomplete",
    }.get(bucket, "Market read is neutral")
    confidence_note = ""
    if adjusted_confidence != raw_confidence:
        confidence_note = f"; confidence {raw_confidence or 'none'} -> {adjusted_confidence or 'none'}"
    gate_note = "" if alert_allowed else f"; suppressed: {', '.join(suppress_reasons)}"
    return f"{label}: edge x{edge_multiplier:.2f}, EV x{ev_multiplier:.2f}{confidence_note}{gate_note}."


def market_respect_for_edge(
    db: Session,
    edge: EdgeResult | None,
    *,
    odds: GameOdds | None = None,
    movement: LineMovement | None = None,
    game: Game | None = None,
    evaluate_freshness: bool = True,
) -> dict:
    """Score whether the market respected our side from open to close.

    The score is intentionally computed on demand from existing odds snapshots
    so it can be used before we decide whether to persist a frozen audit value.
    """
    if edge is None:
        return MarketRespect(
            50,
            ["MARKET NEUTRAL"],
            {
                "reason": "missing_edge",
                "freshness_status": "missing_edge",
                "freshness_reason": "missing edge result",
                "minutes_since_update": None,
            },
        ).as_dict()

    play = (edge.recommended_play or "").lower()
    movement = movement or _movement_for_edge(db, edge)
    game = game or _game_for_edge(db, edge)
    odds = odds or _odds_for_edge(db, edge)
    open_odds = _open_odds(db, edge, odds)
    close_odds = _close_odds(db, edge, odds)
    freshness = (
        odds_freshness_metadata(db, game=game, odds_row=odds)
        if evaluate_freshness
        else {
            "status": "historical_review",
            "reason": "freshness ignored for historical market-respect audit",
            "minutes_since_update": None,
        }
    )

    direction = (edge.movement_direction or "none").lower()
    clv = _edge_clv(edge, play, odds, close_odds)
    sharp_match = _sharp_matches_play(play, movement)
    sharp_against = _sharp_against_play(play, movement)
    late_move = _is_late_move(game, movement, close_odds)
    became_expensive = _became_more_expensive(play, movement)
    disagreement = _sportsbook_disagreement(db, edge.game_id, play)
    line_move = _line_move_for_play(play, movement)

    score = 50.0
    score += _direction_points(direction)
    score += _clv_points(clv)
    score += 14 if sharp_match else 0
    score -= 16 if sharp_against else 0
    score += 8 if late_move and (sharp_match or direction == "toward_model") else 0
    score -= 10 if late_move and (sharp_against or direction == "away_from_model") else 0
    score += 6 if became_expensive else 0
    score += _disagreement_points(disagreement)
    score = max(0, min(100, round(score)))

    tags = _tags(
        score=score,
        direction=direction,
        clv=clv,
        sharp_match=sharp_match,
        sharp_against=sharp_against,
        late_move=late_move,
        disagreement=disagreement,
        movement=movement,
        open_odds=open_odds,
        close_odds=close_odds,
        freshness_status=freshness["status"],
    )

    return MarketRespect(
        score=score,
        tags=tags,
        components={
            "play": play or None,
            "movement_direction": direction,
            "line_move": line_move,
            "price_clv": clv.get("price_clv"),
            "line_clv": clv.get("line_clv"),
            "sharp_match": sharp_match,
            "sharp_against": sharp_against,
            "late_move": late_move,
            "became_more_expensive": became_expensive,
            "sportsbook_disagreement": disagreement,
            "open_snapshot_id": open_odds.id if open_odds else None,
            "close_snapshot_id": close_odds.id if close_odds else None,
            "freshness_status": freshness["status"],
            "freshness_reason": freshness["reason"],
            "minutes_since_update": freshness["minutes_since_update"],
        },
    ).as_dict()


def _movement_for_edge(db: Session, edge: EdgeResult) -> LineMovement | None:
    if edge.movement_id:
        movement = db.query(LineMovement).filter(LineMovement.id == edge.movement_id).first()
        if movement:
            return movement
    return db.query(LineMovement).filter(LineMovement.game_id == edge.game_id).first()


def _game_for_edge(db: Session, edge: EdgeResult) -> Game | None:
    return db.query(Game).filter(Game.game_id == edge.game_id).first()


def _odds_for_edge(db: Session, edge: EdgeResult) -> GameOdds | None:
    return db.query(GameOdds).filter(GameOdds.id == edge.odds_id).first()


def _open_odds(db: Session, edge: EdgeResult, odds: GameOdds | None) -> GameOdds | None:
    book = edge.sportsbook or (odds.sportsbook if odds else None)
    query = db.query(GameOdds).filter(GameOdds.game_id == edge.game_id, GameOdds.snapshot_type == SnapshotType.open)
    if book:
        same = query.filter(GameOdds.sportsbook == book).order_by(GameOdds.fetched_at.asc(), GameOdds.id.asc()).first()
        if same:
            return same
    return query.order_by(GameOdds.fetched_at.asc(), GameOdds.id.asc()).first()


def _close_odds(db: Session, edge: EdgeResult, odds: GameOdds | None) -> GameOdds | None:
    book = edge.sportsbook or (odds.sportsbook if odds else None)
    query = db.query(GameOdds).filter(GameOdds.game_id == edge.game_id, GameOdds.snapshot_type == SnapshotType.pregame)
    if book:
        same = query.filter(GameOdds.sportsbook == book).order_by(GameOdds.fetched_at.desc(), GameOdds.id.desc()).first()
        if same:
            return same
    return query.order_by(GameOdds.fetched_at.desc(), GameOdds.id.desc()).first()


def _play_odds_and_line(odds: GameOdds | None, play: str) -> tuple[int | None, float | None]:
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


def _edge_entry_odds_and_line(edge: EdgeResult, odds: GameOdds | None, play: str) -> tuple[int | None, float | None]:
    if play == "away_ml":
        return edge.away_ml if edge.away_ml is not None else (odds.away_ml if odds else None), None
    if play == "home_ml":
        return edge.home_ml if edge.home_ml is not None else (odds.home_ml if odds else None), None
    if play == "over":
        return (
            edge.over_odds if edge.over_odds is not None else (odds.over_odds if odds else None),
            float(edge.book_total) if edge.book_total is not None else (float(odds.total_line) if odds and odds.total_line is not None else None),
        )
    if play == "under":
        return (
            edge.under_odds if edge.under_odds is not None else (odds.under_odds if odds else None),
            float(edge.book_total) if edge.book_total is not None else (float(odds.total_line) if odds and odds.total_line is not None else None),
        )
    return None, None


def _edge_clv(edge: EdgeResult, play: str, odds: GameOdds | None, close_odds: GameOdds | None) -> dict[str, float | None]:
    entry_odds, entry_line = _edge_entry_odds_and_line(edge, odds, play)
    close_price, close_line = _play_odds_and_line(close_odds, play)
    price_clv = None
    line_clv = None
    if entry_odds is not None and close_price is not None:
        price_clv = round(implied_prob_raw(close_price) - implied_prob_raw(entry_odds), 4)
    if play == "over" and entry_line is not None and close_line is not None:
        line_clv = round(close_line - entry_line, 2)
    elif play == "under" and entry_line is not None and close_line is not None:
        line_clv = round(entry_line - close_line, 2)
    return {"price_clv": price_clv, "line_clv": line_clv}


def _line_move_for_play(play: str, movement: LineMovement | None) -> float | None:
    if movement is None:
        return None
    if play == "away_ml":
        return float(movement.away_prob_move or 0)
    if play == "home_ml":
        return float(movement.home_prob_move or 0)
    if play == "over":
        return float(movement.total_move or 0)
    if play == "under":
        return -float(movement.total_move or 0)
    return None


def _sharp_matches_play(play: str, movement: LineMovement | None) -> bool:
    if movement is None:
        return False
    return (
        (play == "away_ml" and bool(movement.sharp_away))
        or (play == "home_ml" and bool(movement.sharp_home))
        or (play == "over" and bool(movement.total_steam_over))
        or (play == "under" and bool(movement.total_steam_under))
    )


def _sharp_against_play(play: str, movement: LineMovement | None) -> bool:
    if movement is None:
        return False
    return (
        (play == "away_ml" and bool(movement.sharp_home))
        or (play == "home_ml" and bool(movement.sharp_away))
        or (play == "over" and bool(movement.total_steam_under))
        or (play == "under" and bool(movement.total_steam_over))
    )


def _became_more_expensive(play: str, movement: LineMovement | None) -> bool:
    move = _line_move_for_play(play, movement)
    return move is not None and move > (0.015 if play in ML_PLAYS else 0.1)


def _is_late_move(game: Game | None, movement: LineMovement | None, close_odds: GameOdds | None) -> bool:
    if game is None or game.start_time is None:
        return False
    marker = (movement.calculated_at if movement else None) or (close_odds.fetched_at if close_odds else None)
    if marker is None:
        return False
    start = _aware_utc(game.start_time)
    observed = _aware_utc(marker)
    minutes_to_start = (start - observed).total_seconds() / 60
    return 0 <= minutes_to_start <= 120


def _aware_utc(value: datetime) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sportsbook_disagreement(db: Session, game_id: int, play: str) -> float | None:
    rows = (
        db.query(GameOdds)
        .filter(GameOdds.game_id == game_id, GameOdds.snapshot_type == SnapshotType.pregame)
        .all()
    )
    if len(rows) < 2:
        return None
    if play in PRODUCTIVE_TOTAL_PLAYS:
        values = [float(row.total_line) for row in rows if row.total_line is not None]
    elif play == "away_ml":
        values = [implied_prob_raw(row.away_ml) for row in rows if row.away_ml is not None]
    elif play == "home_ml":
        values = [implied_prob_raw(row.home_ml) for row in rows if row.home_ml is not None]
    else:
        values = []
    if len(values) < 2:
        return None
    disagreement = pstdev(values)
    if play in PRODUCTIVE_TOTAL_PLAYS:
        disagreement = disagreement / 10
    return round(disagreement, 4)


def _direction_points(direction: str) -> float:
    if direction == "toward_model":
        return 18
    if direction == "away_from_model":
        return -22
    if direction == "neutral":
        return 0
    return -4


def _clv_points(clv: dict[str, float | None]) -> float:
    points = 0.0
    price = clv.get("price_clv")
    line = clv.get("line_clv")
    if price is not None:
        points += max(-12, min(12, price * 300))
    if line is not None:
        points += max(-14, min(14, line * 8))
    return points


def _disagreement_points(disagreement: float | None) -> float:
    if disagreement is None:
        return -2
    if disagreement <= 0.015:
        return 4
    if disagreement >= 0.04:
        return -8
    return 0


def _tags(
    *,
    score: int,
    direction: str,
    clv: dict[str, float | None],
    sharp_match: bool,
    sharp_against: bool,
    late_move: bool,
    disagreement: float | None,
    movement: LineMovement | None,
    open_odds: GameOdds | None,
    close_odds: GameOdds | None,
    freshness_status: str,
) -> list[str]:
    tags: list[str] = []
    price = clv.get("price_clv")
    line = clv.get("line_clv")
    positive_clv = (price is not None and price > 0) or (line is not None and line > 0)
    negative_clv = (price is not None and price < 0) or (line is not None and line < 0)
    stale = freshness_status in {"stale_feed", "stale_open"}

    if score >= 65 and (direction == "toward_model" or positive_clv or sharp_match):
        tags.append("MARKET AGREED")
    if score <= 40 or direction == "away_from_model" or sharp_against:
        tags.append("MARKET REJECTED")
    if late_move and sharp_match:
        tags.append("LATE SHARP BUY")
    if direction == "toward_model" and not sharp_match and (negative_clv or (disagreement is not None and disagreement >= 0.04)):
        tags.append("FAKE PUBLIC MOVE")
    if stale:
        tags.append("STALE OPEN")
    if freshness_status == "quiet_market":
        tags.append("QUIET MARKET")
    if not tags:
        tags.append("MARKET NEUTRAL")
    return tags
