from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.schema import BetAlert, EdgeResult, Game, GameOdds, Prediction, SnapshotType
from app.services.backtest_service import build_live_feature_vector
from app.services.feature_builder import build_team_features
from app.services.mlb_api import fetch_bullpen_stats, fetch_pitcher_stats, fetch_team_stats
from app.services.statcast_service import fetch_team_statcast

ET = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def get_pipeline_freshness(db: Session, *, target_date: date | None = None) -> dict:
    today = target_date or datetime.now(ET).date()

    latest_game_sync = (
        db.query(func.max(Game.created_at))
        .filter(Game.game_date == today)
        .scalar()
    )
    latest_prediction = (
        db.query(func.max(Prediction.created_at))
        .join(Game, Game.game_id == Prediction.game_id)
        .filter(Game.game_date == today, Prediction.is_active == True)  # noqa: E712
        .scalar()
    )
    latest_open_odds = (
        db.query(func.max(GameOdds.fetched_at))
        .join(Game, Game.game_id == GameOdds.game_id)
        .filter(Game.game_date == today, GameOdds.snapshot_type == SnapshotType.open)
        .scalar()
    )
    latest_pregame_odds = (
        db.query(func.max(GameOdds.fetched_at))
        .join(Game, Game.game_id == GameOdds.game_id)
        .filter(Game.game_date == today, GameOdds.snapshot_type == SnapshotType.pregame)
        .scalar()
    )
    latest_edge = (
        db.query(func.max(EdgeResult.calculated_at))
        .join(Game, Game.game_id == EdgeResult.game_id)
        .filter(Game.game_date == today, EdgeResult.is_active == True)  # noqa: E712
        .scalar()
    )
    latest_alert = (
        db.query(func.max(BetAlert.alert_time))
        .filter(BetAlert.game_date == today)
        .scalar()
    )

    return {
        "date": str(today),
        "games_today": db.query(func.count(Game.game_id)).filter(Game.game_date == today).scalar() or 0,
        "active_predictions_today": (
            db.query(func.count(Prediction.prediction_id))
            .join(Game, Game.game_id == Prediction.game_id)
            .filter(Game.game_date == today, Prediction.is_active == True)  # noqa: E712
            .scalar()
            or 0
        ),
        "active_edges_today": (
            db.query(func.count(EdgeResult.id))
            .join(Game, Game.game_id == EdgeResult.game_id)
            .filter(Game.game_date == today, EdgeResult.is_active == True)  # noqa: E712
            .scalar()
            or 0
        ),
        "alerts_today": db.query(func.count(BetAlert.id)).filter(BetAlert.game_date == today).scalar() or 0,
        "last_game_sync": _iso_or_none(latest_game_sync),
        "last_prediction_run": _iso_or_none(latest_prediction),
        "last_open_odds_sync": _iso_or_none(latest_open_odds),
        "last_pregame_odds_sync": _iso_or_none(latest_pregame_odds),
        "last_edge_calc": _iso_or_none(latest_edge),
        "last_alert_run": _iso_or_none(latest_alert),
    }


def backfill_prediction_dashboard_metrics(
    db: Session,
    *,
    target_date: date | None = None,
    active_only: bool = True,
) -> dict:
    today = target_date or datetime.now(ET).date()
    query = (
        db.query(Prediction, Game)
        .join(Game, Game.game_id == Prediction.game_id)
        .filter(Game.game_date == today)
    )
    if active_only:
        query = query.filter(Prediction.is_active == True)  # noqa: E712

    updated = 0
    skipped = 0
    errors: list[dict] = []

    for prediction, game in query.all():
        if (
            prediction.kbb_adv is not None
            and prediction.park_factor_adv is not None
            and prediction.pythagorean_win_pct_adv is not None
        ):
            skipped += 1
            continue

        try:
            away_raw = fetch_team_stats(team_id=game.away_team_id, season=game.season)
            home_raw = fetch_team_stats(team_id=game.home_team_id, season=game.season)
            away_starter = fetch_pitcher_stats(game.away_pitcher_id, game.season, include_xera=True) if game.away_pitcher_id else None
            home_starter = fetch_pitcher_stats(game.home_pitcher_id, game.season, include_xera=True) if game.home_pitcher_id else None
            away_bullpen = fetch_bullpen_stats(game.away_team_id, game.season)
            home_bullpen = fetch_bullpen_stats(game.home_team_id, game.season)
            away_statcast = fetch_team_statcast(game.away_team_id, game.season)
            home_statcast = fetch_team_statcast(game.home_team_id, game.season)

            away_features = build_team_features(
                away_raw,
                starter_stats=away_starter,
                bullpen_stats=away_bullpen,
                statcast_team=away_statcast,
            )
            home_features = build_team_features(
                home_raw,
                starter_stats=home_starter,
                venue=game.venue,
                bullpen_stats=home_bullpen,
                statcast_team=home_statcast,
            )
            live_features = build_live_feature_vector(home_features, away_features)

            prediction.kbb_adv = live_features.get("kbb_adv")
            prediction.park_factor_adv = live_features.get("park_factor_adv")
            prediction.pythagorean_win_pct_adv = live_features.get("pythagorean_win_pct_adv")
            updated += 1
        except (RuntimeError, ValueError) as exc:
            db.rollback()
            logger.warning(
                "Dashboard metrics backfill failed for game %s prediction %s",
                game.game_id,
                prediction.prediction_id,
                exc_info=exc,
            )
            errors.append(
                {
                    "game_id": game.game_id,
                    "prediction_id": prediction.prediction_id,
                    "error": str(exc),
                }
            )

    db.commit()
    return {
        "date": str(today),
        "updated": updated,
        "skipped_already_populated": skipped,
        "errors": errors,
    }
