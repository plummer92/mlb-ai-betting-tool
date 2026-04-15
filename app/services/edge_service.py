from datetime import datetime, timezone
import logging
from zoneinfo import ZoneInfo

from scipy.stats import norm
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.schema import EdgeResult, Game, GameOdds, LineMovement, Prediction
from app.services.ev_math import (
    american_to_decimal,
    calc_edge,
    calc_ev,
    confidence_tier,
    implied_prob_raw,
    movement_ev_boost,
    recommended_play,
    remove_vig,
)
from app.services.odds_service import SnapshotType, get_latest_odds_snapshot, is_odds_snapshot_fresh

ET = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)

# Typical MLB run total standard deviation — tune once you have data
TOTAL_STD_DEV = 2.5
SANE_EV_BOUNDS = (-0.95, 1.0)
ALLOWED_ACTIVE_EDGE_STAGES: dict[str, SnapshotType] = {
    "daily_open": SnapshotType.open,
    "pregame": SnapshotType.pregame,
}


def _compile_query_sql(db: Session, query) -> str:
    bind = db.get_bind()
    if bind is None:
        return str(query.statement)
    return str(
        query.statement.compile(
            dialect=bind.dialect,
            compile_kwargs={"literal_binds": True},
        )
    )


def _odds_row_debug_payload(odds: GameOdds | None, *, source: str) -> dict | None:
    if odds is None:
        return None
    return {
        "source": source,
        "id": odds.id,
        "game_id": odds.game_id,
        "sportsbook": odds.sportsbook,
        "snapshot_type": odds.snapshot_type.value if odds.snapshot_type else None,
        "fetched_at": odds.fetched_at.isoformat() if odds.fetched_at else None,
    }


def _odds_freshness_debug(row: GameOdds | None) -> dict | None:
    if row is None or row.fetched_at is None:
        return None
    fetched_at = row.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    max_age_minutes = 180 if row.snapshot_type == SnapshotType.open else 90 if row.snapshot_type == SnapshotType.pregame else 15
    age_seconds = round((now_utc - fetched_at).total_seconds(), 3)
    return {
        "now_utc": now_utc.isoformat(),
        "fetched_at_utc": fetched_at.isoformat(),
        "age_seconds": age_seconds,
        "max_age_minutes": max_age_minutes,
        "is_fresh": 0 <= age_seconds <= (max_age_minutes * 60),
    }


def _invalid_result(
    *,
    game_id: int,
    run_stage: str,
    reason: str,
    detail: str | None = None,
) -> dict:
    payload = {
        "status": "skipped",
        "game_id": game_id,
        "run_stage": run_stage,
        "reason": reason,
    }
    if detail:
        payload["detail"] = detail
    logger.warning(
        "[edge] skipped game=%s stage=%s reason=%s detail=%s",
        game_id,
        run_stage,
        reason,
        detail,
    )
    return payload


def _is_valid_probability(value: float | None) -> bool:
    return value is not None and 0.0 < float(value) < 1.0


def _is_valid_decimal_odds(value: float | None) -> bool:
    return value is not None and float(value) > 1.0


def _is_sane_ev(value: float | None) -> bool:
    return value is not None and SANE_EV_BOUNDS[0] <= float(value) <= SANE_EV_BOUNDS[1]


def validate_active_edge_lineage(
    edge: EdgeResult,
    prediction: Prediction,
    odds: GameOdds,
) -> tuple[bool, str | None]:
    expected_snapshot_type = ALLOWED_ACTIVE_EDGE_STAGES.get(edge.run_stage)
    if expected_snapshot_type is None:
        return False, "disallowed_run_stage"
    if not edge.is_active:
        return False, "inactive_edge"
    if not prediction.is_active:
        return False, "inactive_prediction"
    if prediction.run_stage != edge.run_stage:
        return False, "prediction_stage_mismatch"
    if odds.id != edge.odds_id:
        return False, "odds_snapshot_mismatch"
    if odds.snapshot_type != expected_snapshot_type:
        return False, "odds_snapshot_type_mismatch"
    if not is_odds_snapshot_fresh(odds):
        return False, "stale_odds_snapshot"
    return True, None


def quarantine_untrustworthy_active_edges(
    db: Session,
    *,
    game_date=None,
) -> dict:
    rows = (
        db.query(EdgeResult, Prediction, GameOdds, Game)
        .join(Prediction, Prediction.prediction_id == EdgeResult.prediction_id)
        .join(GameOdds, GameOdds.id == EdgeResult.odds_id)
        .join(Game, Game.game_id == EdgeResult.game_id)
        .filter(EdgeResult.is_active == True)  # noqa: E712
    )
    if game_date is not None:
        rows = rows.filter(Game.game_date == game_date)

    invalid_ids: list[int] = []
    reasons: dict[str, int] = {}
    for edge, prediction, odds, _game in rows.all():
        is_valid, reason = validate_active_edge_lineage(edge, prediction, odds)
        if is_valid:
            continue
        invalid_ids.append(edge.id)
        reasons[reason or "invalid_edge"] = reasons.get(reason or "invalid_edge", 0) + 1

    if invalid_ids:
        (
            db.query(EdgeResult)
            .filter(EdgeResult.id.in_(invalid_ids))
            .update({"is_active": False}, synchronize_session=False)
        )
        db.commit()

    return {"deactivated": len(invalid_ids), "reasons": reasons}


def get_trustworthy_active_edges(
    db: Session,
    *,
    game_date=None,
) -> list[tuple[EdgeResult, Game, Prediction, GameOdds]]:
    quarantine_untrustworthy_active_edges(db, game_date=game_date)

    rows = (
        db.query(EdgeResult, Game, Prediction, GameOdds)
        .join(Game, Game.game_id == EdgeResult.game_id)
        .join(Prediction, Prediction.prediction_id == EdgeResult.prediction_id)
        .join(GameOdds, GameOdds.id == EdgeResult.odds_id)
        .filter(
            EdgeResult.is_active == True,  # noqa: E712
            Prediction.is_active == True,  # noqa: E712
        )
    )
    if game_date is not None:
        rows = rows.filter(Game.game_date == game_date)

    trusted_rows: list[tuple[EdgeResult, Game, Prediction, GameOdds]] = []
    for edge, game, prediction, odds in rows.order_by(EdgeResult.calculated_at.desc()).all():
        is_valid, _reason = validate_active_edge_lineage(edge, prediction, odds)
        if is_valid:
            trusted_rows.append((edge, game, prediction, odds))
    return trusted_rows


def _pick_odds_snapshot_for_game(
    db: Session,
    *,
    game_id: int,
    snapshot_type: SnapshotType,
    explicit_odds: GameOdds | None,
    run_stage: str,
    fallback_policy: str,
) -> tuple[GameOdds | None, str | None]:
    odds_query = (
        db.query(GameOdds)
        .filter(
            GameOdds.game_id == game_id,
            GameOdds.snapshot_type == snapshot_type,
        )
        .order_by(GameOdds.fetched_at.desc(), GameOdds.id.desc())
    )
    db_rows = odds_query.all() if fallback_policy == "reuse_fresh_same_stage" else []
    logger.info(
        "[edge] odds lookup game=%s stage=%s snapshot=%s fallback=%s sql=%s",
        game_id,
        run_stage,
        snapshot_type.value,
        fallback_policy,
        _compile_query_sql(db, odds_query),
    )

    rejection_reasons: list[dict] = []
    valid_candidates: list[tuple[GameOdds, str]] = []
    seen_ids: set[int] = set()

    def _evaluate_candidate(row: GameOdds, *, source: str) -> None:
        if row.id is not None and row.id in seen_ids:
            return
        if row.id is not None:
            seen_ids.add(row.id)

        reason = None
        if row.game_id != game_id:
            reason = "explicit_snapshot_game_mismatch" if source == "explicit" else "db_snapshot_game_mismatch"
        elif row.snapshot_type != snapshot_type:
            reason = "explicit_snapshot_type_mismatch" if source == "explicit" else "db_snapshot_type_mismatch"
        elif not is_odds_snapshot_fresh(row):
            reason = "stale_explicit_odds_snapshot" if source == "explicit" else "stale_existing_odds_snapshot"

        payload = _odds_row_debug_payload(row, source=source)
        freshness = _odds_freshness_debug(row)
        if reason:
            logger.info(
                "[edge] odds candidate rejected game=%s stage=%s source=%s reason=%s row=%s freshness=%s",
                game_id,
                run_stage,
                source,
                reason,
                payload,
                freshness,
            )
            rejection_reasons.append({"reason": reason, "row": payload})
            return
        logger.info(
            "[edge] odds candidate accepted game=%s stage=%s source=%s row=%s freshness=%s",
            game_id,
            run_stage,
            source,
            payload,
            freshness,
        )
        valid_candidates.append((row, source))

    if explicit_odds is not None:
        _evaluate_candidate(explicit_odds, source="explicit")

    for row in db_rows:
        _evaluate_candidate(row, source="db")

    logger.info(
        "[edge] odds candidates game=%s stage=%s explicit=%s db_rows=%s valid=%s rejected=%s",
        game_id,
        run_stage,
        _odds_row_debug_payload(explicit_odds, source="explicit"),
        [_odds_row_debug_payload(row, source="db") for row in db_rows],
        [_odds_row_debug_payload(row, source=source) for row, source in valid_candidates],
        rejection_reasons,
    )

    if valid_candidates:
        best_row, best_source = max(
            valid_candidates,
            key=lambda item: (
                item[0].fetched_at or datetime.min.replace(tzinfo=timezone.utc),
                item[0].id or 0,
            ),
        )
        logger.info(
            "[edge] selected odds game=%s stage=%s source=%s odds_id=%s sportsbook=%s fetched_at=%s",
            game_id,
            run_stage,
            best_source,
            best_row.id,
            best_row.sportsbook,
            best_row.fetched_at.isoformat() if best_row.fetched_at else None,
        )
        return best_row, None

    if explicit_odds is None and fallback_policy != "reuse_fresh_same_stage":
        logger.info(
            "[edge] odds selection failed game=%s stage=%s reason=missing_explicit_odds_snapshot",
            game_id,
            run_stage,
        )
        return None, "missing_explicit_odds_snapshot"
    if explicit_odds is not None and fallback_policy != "reuse_fresh_same_stage":
        explicit_reason = next(
            (item["reason"] for item in rejection_reasons if item["row"] and item["row"]["source"] == "explicit"),
            None,
        )
        logger.info(
            "[edge] odds selection failed game=%s stage=%s reason=%s",
            game_id,
            run_stage,
            explicit_reason or "missing_explicit_odds_snapshot",
        )
        return None, explicit_reason or "missing_explicit_odds_snapshot"
    if not db_rows:
        logger.info(
            "[edge] odds selection failed game=%s stage=%s reason=missing_odds_snapshot",
            game_id,
            run_stage,
        )
        return None, "missing_odds_snapshot"
    logger.info(
        "[edge] odds selection failed game=%s stage=%s reason=stale_existing_odds_snapshot",
        game_id,
        run_stage,
    )
    return None, "stale_existing_odds_snapshot"


def calculate_edge_for_game(
    db: Session,
    game_id: int,
    *,
    run_stage: str,
    snapshot_type: SnapshotType,
    odds_snapshot: GameOdds | None = None,
    fallback_policy: str = "none",
    movement: LineMovement | None = None,
) -> dict:
    """
    Pull latest prediction + latest odds snapshot for a game,
    run all EV math, persist and return the EdgeResult.
    """
    expected_snapshot_type = ALLOWED_ACTIVE_EDGE_STAGES.get(run_stage)
    if expected_snapshot_type is None:
        return _invalid_result(game_id=game_id, run_stage=run_stage, reason="disallowed_run_stage")
    if snapshot_type != expected_snapshot_type:
        return _invalid_result(game_id=game_id, run_stage=run_stage, reason="run_stage_snapshot_mismatch")

    prediction = (
        db.query(Prediction)
        .filter(
            Prediction.game_id == game_id,
            Prediction.run_stage == run_stage,
            Prediction.is_active == True,  # noqa: E712
        )
        .order_by(desc(Prediction.created_at))
        .first()
    )
    logger.info(
        "[edge] prediction lookup game=%s stage=%s snapshot=%s explicit_odds_id=%s fallback=%s",
        game_id,
        run_stage,
        snapshot_type.value,
        odds_snapshot.id if odds_snapshot else None,
        fallback_policy,
    )
    if not prediction:
        return _invalid_result(game_id=game_id, run_stage=run_stage, reason="missing_active_prediction")

    odds, odds_reason = _pick_odds_snapshot_for_game(
        db,
        game_id=game_id,
        snapshot_type=snapshot_type,
        explicit_odds=odds_snapshot,
        run_stage=run_stage,
        fallback_policy=fallback_policy,
    )
    if odds is None:
        return _invalid_result(game_id=game_id, run_stage=run_stage, reason=odds_reason or "missing_odds_snapshot")

    # ── Moneyline math ──────────────────────────────────
    raw_away = implied_prob_raw(odds.away_ml)
    raw_home = implied_prob_raw(odds.home_ml)
    if not _is_valid_probability(raw_away) or not _is_valid_probability(raw_home):
        return _invalid_result(game_id=game_id, run_stage=run_stage, reason="invalid_implied_probability")

    imp_away, imp_home = remove_vig(raw_away, raw_home)
    if not _is_valid_probability(imp_away) or not _is_valid_probability(imp_home):
        return _invalid_result(game_id=game_id, run_stage=run_stage, reason="invalid_vig_removed_probability")

    # Prefer calibrated probabilities; fall back to raw Monte Carlo output
    model_away = float(prediction.calibrated_away_win_pct or prediction.away_win_pct)
    model_home = float(prediction.calibrated_home_win_pct or prediction.home_win_pct)
    if not _is_valid_probability(model_away) or not _is_valid_probability(model_home):
        return _invalid_result(
            game_id=game_id,
            run_stage=run_stage,
            reason="invalid_model_probability",
            detail=f"away={model_away} home={model_home}",
        )

    away_decimal = american_to_decimal(odds.away_ml)
    home_decimal = american_to_decimal(odds.home_ml)
    if not _is_valid_decimal_odds(away_decimal) or not _is_valid_decimal_odds(home_decimal):
        return _invalid_result(game_id=game_id, run_stage=run_stage, reason="invalid_decimal_odds")

    edge_away = calc_edge(model_away, imp_away)
    edge_home = calc_edge(model_home, imp_home)
    ev_away = calc_ev(model_away, away_decimal)
    ev_home = calc_ev(model_home, home_decimal)
    if not _is_sane_ev(ev_away) or not _is_sane_ev(ev_home):
        return _invalid_result(
            game_id=game_id,
            run_stage=run_stage,
            reason="invalid_moneyline_ev",
            detail=f"ev_away={ev_away} ev_home={ev_home}",
        )

    # ── Totals math ─────────────────────────────────────
    ev_over = ev_under = total_edge = None
    edge_over = edge_under = None

    if odds.total_line and odds.over_odds and odds.under_odds:
        model_total = float(prediction.projected_total)
        book_total = float(odds.total_line)

        # Normal distribution approximation for over/under probability.
        model_over_prob = float(1 - norm.cdf(book_total, loc=model_total, scale=TOTAL_STD_DEV))
        model_under_prob = 1 - model_over_prob
        if not _is_valid_probability(model_over_prob) or not _is_valid_probability(model_under_prob):
            return _invalid_result(game_id=game_id, run_stage=run_stage, reason="invalid_total_probability")

        raw_over = implied_prob_raw(odds.over_odds)
        raw_under = implied_prob_raw(odds.under_odds)
        if not _is_valid_probability(raw_over) or not _is_valid_probability(raw_under):
            return _invalid_result(game_id=game_id, run_stage=run_stage, reason="invalid_total_implied_probability")

        imp_over, imp_under = remove_vig(raw_over, raw_under)
        if not _is_valid_probability(imp_over) or not _is_valid_probability(imp_under):
            return _invalid_result(game_id=game_id, run_stage=run_stage, reason="invalid_total_vig_removed_probability")

        over_decimal = american_to_decimal(odds.over_odds)
        under_decimal = american_to_decimal(odds.under_odds)
        if not _is_valid_decimal_odds(over_decimal) or not _is_valid_decimal_odds(under_decimal):
            return _invalid_result(game_id=game_id, run_stage=run_stage, reason="invalid_total_decimal_odds")

        edge_over  = calc_edge(model_over_prob,  imp_over)
        edge_under = calc_edge(model_under_prob, imp_under)

        ev_over = calc_ev(model_over_prob, over_decimal)
        ev_under = calc_ev(model_under_prob, under_decimal)
        if not _is_sane_ev(ev_over) or not _is_sane_ev(ev_under):
            return _invalid_result(
                game_id=game_id,
                run_stage=run_stage,
                reason="invalid_total_ev",
                detail=f"ev_over={ev_over} ev_under={ev_under}",
            )
        total_edge = model_total - book_total

    # ── Movement signal ──────────────────────────────────
    if movement is None:
        movement = db.query(LineMovement).filter(LineMovement.game_id == game_id).first()

    away_boost = home_boost = 0.0
    movement_id = None

    if movement:
        movement_id = movement.id
        sharp_agrees_away = movement.sharp_away and (model_away > imp_away)
        sharp_against_away = movement.sharp_home and (model_away <= imp_away)

        sharp_agrees_home = movement.sharp_home and (model_home > imp_home)
        sharp_against_home = movement.sharp_away and (model_home <= imp_home)

        away_boost = movement_ev_boost(model_away, sharp_agrees_away, sharp_against_away)
        home_boost = movement_ev_boost(model_home, sharp_agrees_home, sharp_against_home)

    ev_away_final = ev_away + away_boost
    ev_home_final = ev_home + home_boost
    net_boost = away_boost + home_boost

    # ── Best play + tier ────────────────────────────────
    play = recommended_play(
        edge_away, ev_away_final,
        edge_home, ev_home_final,
        edge_over or 0.0, ev_over or 0.0,
        edge_under or 0.0, ev_under or 0.0,
        model_away=model_away,
        model_home=model_home,
    )
    max_edge = max(abs(edge_away), abs(edge_home), abs(edge_over or 0.0), abs(edge_under or 0.0))
    max_ev = max(ev_away_final, ev_home_final, ev_over or 0.0, ev_under or 0.0)
    tier = confidence_tier(max_edge, max_ev)
    if not play:
        return _invalid_result(game_id=game_id, run_stage=run_stage, reason="no_qualifying_play")

    # ── Movement direction relative to model recommendation ──
    movement_direction: str | None = None
    if movement:
        model_prefers_away = model_away > model_home
        if model_prefers_away:
            if movement.sharp_away:
                movement_direction = "toward_model"
            elif movement.sharp_home:
                movement_direction = "away_from_model"
            else:
                movement_direction = "neutral"
        else:
            if movement.sharp_home:
                movement_direction = "toward_model"
            elif movement.sharp_away:
                movement_direction = "away_from_model"
            else:
                movement_direction = "neutral"

    # Upsert: if an EdgeResult already exists for this (game_id, prediction_id)
    # pair — which happens when store_prediction reuses the same prediction_id
    # via its own upsert — UPDATE it in place to avoid violating the
    # uq_edge_game_prediction unique constraint.
    existing_edge = (
        db.query(EdgeResult)
        .filter(
            EdgeResult.game_id == game_id,
            EdgeResult.prediction_id == prediction.prediction_id,
        )
        .first()
    )

    if existing_edge:
        existing_edge.odds_id = odds.id
        existing_edge.run_stage = run_stage
        existing_edge.is_active = True
        existing_edge.movement_id = movement_id
        existing_edge.calculated_at = datetime.now(timezone.utc)
        existing_edge.model_away_win_pct = round(model_away, 4)
        existing_edge.model_home_win_pct = round(model_home, 4)
        existing_edge.implied_away_pct = round(imp_away, 4)
        existing_edge.implied_home_pct = round(imp_home, 4)
        existing_edge.edge_away = round(edge_away, 4)
        existing_edge.edge_home = round(edge_home, 4)
        existing_edge.ev_away = round(ev_away_final, 4)
        existing_edge.ev_home = round(ev_home_final, 4)
        existing_edge.movement_boost = round(net_boost, 4)
        existing_edge.model_total = prediction.projected_total
        existing_edge.book_total = odds.total_line
        existing_edge.total_edge = round(total_edge, 4) if total_edge is not None else None
        existing_edge.ev_over = round(ev_over, 4) if ev_over is not None else None
        existing_edge.ev_under = round(ev_under, 4) if ev_under is not None else None
        existing_edge.recommended_play = play
        existing_edge.confidence_tier = tier
        existing_edge.edge_pct = round(max_edge, 4)
        existing_edge.movement_direction = movement_direction
        db.commit()
        db.refresh(existing_edge)
        return {
            "status": "created",
            "game_id": game_id,
            "run_stage": run_stage,
            "edge": existing_edge,
            "odds_id": odds.id,
        }

    # No existing edge for this prediction — deactivate any stale active edges
    # for this game+stage (from a different prediction_id), then insert fresh.
    (
        db.query(EdgeResult)
        .filter(
            EdgeResult.game_id == game_id,
            EdgeResult.run_stage == run_stage,
            EdgeResult.is_active == True,  # noqa: E712
        )
        .update({"is_active": False}, synchronize_session=False)
    )
    edge = EdgeResult(
        game_id=game_id,
        prediction_id=prediction.prediction_id,
        odds_id=odds.id,
        run_stage=run_stage,
        is_active=True,
        movement_id=movement_id,
        calculated_at=datetime.now(timezone.utc),
        model_away_win_pct=round(model_away, 4),
        model_home_win_pct=round(model_home, 4),
        implied_away_pct=round(imp_away, 4),
        implied_home_pct=round(imp_home, 4),
        edge_away=round(edge_away, 4),
        edge_home=round(edge_home, 4),
        ev_away=round(ev_away_final, 4),
        ev_home=round(ev_home_final, 4),
        movement_boost=round(net_boost, 4),
        model_total=prediction.projected_total,
        book_total=odds.total_line,
        total_edge=round(total_edge, 4) if total_edge is not None else None,
        ev_over=round(ev_over, 4) if ev_over is not None else None,
        ev_under=round(ev_under, 4) if ev_under is not None else None,
        recommended_play=play,
        confidence_tier=tier,
        edge_pct=round(max_edge, 4),
        movement_direction=movement_direction,
    )
    db.add(edge)
    db.commit()
    db.refresh(edge)

    return {
        "status": "created",
        "game_id": game_id,
        "run_stage": run_stage,
        "edge": edge,
        "odds_id": odds.id,
    }


def calculate_all_edges_today(
    db: Session,
    *,
    run_stage: str,
    snapshot_type: SnapshotType,
    odds_rows: list[GameOdds] | None = None,
    fallback_policy: str = "none",
) -> list[dict]:
    today = datetime.now(ET).date()
    games = db.query(Game).filter(Game.game_date == today).all()
    odds_by_game = {row.game_id: row for row in (odds_rows or [])}
    results: list[dict] = []
    for game in games:
        explicit = odds_by_game.get(game.game_id)
        logger.info(
            "[edge] evaluating game=%s stage=%s snapshot=%s explicit_odds=%s explicit_rows_available=%s",
            game.game_id,
            run_stage,
            snapshot_type.value,
            _odds_row_debug_payload(explicit, source="explicit") if explicit else None,
            sorted(odds_by_game.keys()),
        )
        result = calculate_edge_for_game(
            db,
            game.game_id,
            run_stage=run_stage,
            snapshot_type=snapshot_type,
            odds_snapshot=explicit,
            fallback_policy=fallback_policy,
        )
        results.append(result)
    return results
