from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.schema import Game, Prediction


def deactivate_stale_active_predictions(
    db: Session,
    *,
    keep_on_or_after: date,
) -> int:
    stale_game_ids = (
        db.query(Game.game_id)
        .filter(Game.game_date < keep_on_or_after)
        .subquery()
    )
    deactivated = (
        db.query(Prediction)
        .filter(
            Prediction.is_active == True,  # noqa: E712
            Prediction.game_id.in_(select(stale_game_ids.c.game_id)),
        )
        .update({"is_active": False}, synchronize_session=False)
    )
    if deactivated:
        db.commit()
    return int(deactivated or 0)


def store_prediction(
    db: Session,
    *,
    game_id: int,
    model_version: str,
    run_stage: str,
    sim_count: int,
    away_win_pct: float,
    home_win_pct: float,
    calibrated_home_win_pct: float | None,
    calibrated_away_win_pct: float | None,
    projected_away_score: float,
    projected_home_score: float,
    projected_total: float,
    confidence_score: float,
    recommended_side: str | None,
    home_starter_xera: float | None,
    away_starter_xera: float | None,
    using_xera: bool,
    kbb_adv: float | None,
    park_factor_adv: float | None,
    pythagorean_win_pct_adv: float | None,
    calibration_result_id: int | None,
) -> Prediction:
    # Upsert: if an active prediction already exists for this game+stage today,
    # update it in place to avoid duplicate rows from the pipeline running twice.
    existing = (
        db.query(Prediction)
        .filter(
            Prediction.game_id == game_id,
            Prediction.run_stage == run_stage,
            Prediction.is_active == True,  # noqa: E712
        )
        .order_by(Prediction.created_at.desc())
        .first()
    )
    if existing:
        existing.model_version = model_version
        existing.sim_count = sim_count
        existing.calibration_result_id = calibration_result_id
        existing.away_win_pct = away_win_pct
        existing.home_win_pct = home_win_pct
        existing.calibrated_home_win_pct = calibrated_home_win_pct
        existing.calibrated_away_win_pct = calibrated_away_win_pct
        existing.projected_away_score = projected_away_score
        existing.projected_home_score = projected_home_score
        existing.projected_total = projected_total
        existing.confidence_score = confidence_score
        existing.recommended_side = recommended_side
        existing.home_starter_xera = home_starter_xera
        existing.away_starter_xera = away_starter_xera
        existing.using_xera = using_xera
        existing.kbb_adv = kbb_adv
        existing.park_factor_adv = park_factor_adv
        existing.pythagorean_win_pct_adv = pythagorean_win_pct_adv
        db.commit()
        db.refresh(existing)
        return existing

    # No active prediction yet — retire any legacy rows, then insert a fresh one.
    legacy_query = (
        db.query(Prediction)
        .filter(
            Prediction.game_id == game_id,
            Prediction.is_active == True,  # noqa: E712
        )
    )
    if run_stage != "legacy":
        legacy_query = legacy_query.filter(Prediction.run_stage == "legacy")
        legacy_query.update({"is_active": False}, synchronize_session=False)

    prediction = Prediction(
        game_id=game_id,
        model_version=model_version,
        run_stage=run_stage,
        is_active=True,
        sim_count=sim_count,
        calibration_result_id=calibration_result_id,
        away_win_pct=away_win_pct,
        home_win_pct=home_win_pct,
        calibrated_home_win_pct=calibrated_home_win_pct,
        calibrated_away_win_pct=calibrated_away_win_pct,
        projected_away_score=projected_away_score,
        projected_home_score=projected_home_score,
        projected_total=projected_total,
        confidence_score=confidence_score,
        recommended_side=recommended_side,
        home_starter_xera=home_starter_xera,
        away_starter_xera=away_starter_xera,
        using_xera=using_xera,
        kbb_adv=kbb_adv,
        park_factor_adv=park_factor_adv,
        pythagorean_win_pct_adv=pythagorean_win_pct_adv,
    )
    db.add(prediction)
    db.commit()
    db.refresh(prediction)
    return prediction
