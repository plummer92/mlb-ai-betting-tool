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
    calibration_result_id: int | None,
) -> Prediction:
    # Preserve history, but deactivate stale active lineage before inserting
    # the replacement row. Non-legacy writers also retire legacy rows for the
    # same game so the active set reflects the newest staged pipeline.
    active_query = (
        db.query(Prediction)
        .filter(
            Prediction.game_id == game_id,
            Prediction.is_active == True,  # noqa: E712
        )
    )
    if run_stage == "legacy":
        active_query = active_query.filter(Prediction.run_stage == run_stage)
    else:
        active_query = active_query.filter(
            or_(
                Prediction.run_stage == run_stage,
                Prediction.run_stage == "legacy",
            )
        )
    active_query.update({"is_active": False}, synchronize_session=False)

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
    )
    db.add(prediction)
    db.commit()
    db.refresh(prediction)
    return prediction
