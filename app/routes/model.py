import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import Game, Prediction

ET = ZoneInfo("America/New_York")
from app.models.pydantic_models import PredictionOut
from app.services.backtest_service import (
    apply_calibration,
    build_live_feature_vector,
    get_latest_calibration_result,
    score_logistic_home_probability,
)
from app.services.feature_builder import build_team_features
from app.services.mlb_api import fetch_bullpen_stats, fetch_pitcher_stats, fetch_team_stats
from app.services.model_diagnostics import summarize_probability_diagnostics
from app.services.odds_service import (
    SnapshotType,
    get_latest_odds_snapshot,
    get_market_home_probability,
    is_odds_snapshot_fresh,
)
from app.services.prediction_service import store_prediction
from app.services.simulator import MODEL_VERSION, run_monte_carlo
from app.services.statcast_service import fetch_team_statcast

router = APIRouter(prefix="/api/model", tags=["model"])


@router.post("/run/{game_id}", response_model=PredictionOut)
def run_model(game_id: int, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.game_id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

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
    latest_odds = get_latest_odds_snapshot(db, game_id=game.game_id, snapshot_type=SnapshotType.pregame)
    if latest_odds is None or not is_odds_snapshot_fresh(latest_odds):
        latest_odds = get_latest_odds_snapshot(db, game_id=game.game_id, snapshot_type=SnapshotType.open)
    market_home_prob = get_market_home_probability(latest_odds) if latest_odds and is_odds_snapshot_fresh(latest_odds) else None

    cal_result = get_latest_calibration_result(db)
    cal_params = json.loads(cal_result.calibration_params_json) if cal_result and cal_result.calibration_params_json else None
    live_features = build_live_feature_vector(home_features, away_features)
    logistic_home_prob = score_logistic_home_probability(
        live_features,
        cal_result,
    )
    result = run_monte_carlo(
        away_team=away_features,
        home_team=home_features,
        sim_count=1000,
        market_home_prob=market_home_prob,
        logistic_home_prob=logistic_home_prob,
    )
    cal_home = cal_away = None
    if cal_params:
        cal_home, cal_away = apply_calibration(
            result["home_win_pct"],
            result["away_win_pct"],
            cal_params,
        )
    summarize_probability_diagnostics([result], label=f"manual-game-{game_id}")

    prediction = store_prediction(
        db,
        game_id=game.game_id,
        model_version=MODEL_VERSION,
        run_stage="manual",
        sim_count=result["sim_count"],
        away_win_pct=result["away_win_pct"],
        home_win_pct=result["home_win_pct"],
        calibrated_home_win_pct=cal_home,
        calibrated_away_win_pct=cal_away,
        projected_away_score=result["projected_away_score"],
        projected_home_score=result["projected_home_score"],
        projected_total=result["projected_total"],
        confidence_score=result["confidence_score"],
        recommended_side=result["recommended_side"],
        home_starter_xera=home_features.get("starter_xera"),
        away_starter_xera=away_features.get("starter_xera"),
        using_xera=bool(home_features.get("using_xera") or away_features.get("using_xera")),
        kbb_adv=live_features.get("kbb_adv"),
        park_factor_adv=live_features.get("park_factor_adv"),
        pythagorean_win_pct_adv=live_features.get("pythagorean_win_pct_adv"),
        calibration_result_id=cal_result.id if cal_result else None,
    )

    return prediction


@router.get("/predictions/today")
def get_today_predictions(db: Session = Depends(get_db)):
    today = datetime.now(ET).date()

    subq = (
        db.query(
            Prediction.game_id,
            func.max(Prediction.prediction_id).label("max_id"),
        )
        .join(Game, Prediction.game_id == Game.game_id)
        .filter(Game.game_date == today, Prediction.is_active == True)  # noqa: E712
        .group_by(Prediction.game_id)
        .subquery()
    )

    rows = (
        db.query(Prediction)
        .join(subq, Prediction.prediction_id == subq.c.max_id)
        .all()
    )

    return [
        {
            "game_id": r.game_id,
            "model_version": r.model_version,
            "away_win_pct": r.away_win_pct,
            "home_win_pct": r.home_win_pct,
            "projected_away_score": r.projected_away_score,
            "projected_home_score": r.projected_home_score,
            "projected_total": r.projected_total,
            "confidence_score": r.confidence_score,
            "recommended_side": r.recommended_side,
            "kbb_adv": r.kbb_adv,
            "park_factor_adv": r.park_factor_adv,
            "pythagorean_win_pct_adv": r.pythagorean_win_pct_adv,
        }
        for r in rows
    ]
