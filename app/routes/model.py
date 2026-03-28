from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import Game, Prediction

ET = ZoneInfo("America/New_York")
from app.models.pydantic_models import PredictionOut
from app.services.feature_builder import build_team_features
from app.services.mlb_api import fetch_team_stats
from app.services.simulator import MODEL_VERSION, run_monte_carlo

router = APIRouter(prefix="/api/model", tags=["model"])


@router.post("/run/{game_id}", response_model=PredictionOut)
def run_model(game_id: int, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.game_id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    away_raw = fetch_team_stats(team_id=game.away_team_id, season=game.season)
    home_raw = fetch_team_stats(team_id=game.home_team_id, season=game.season)

    away_features = build_team_features(away_raw)
    home_features = build_team_features(home_raw)

    result = run_monte_carlo(
        away_team=away_features,
        home_team=home_features,
        sim_count=1000,
    )

    prediction = Prediction(
        game_id=game.game_id,
        model_version=MODEL_VERSION,
        sim_count=result["sim_count"],
        away_win_pct=result["away_win_pct"],
        home_win_pct=result["home_win_pct"],
        projected_away_score=result["projected_away_score"],
        projected_home_score=result["projected_home_score"],
        projected_total=result["projected_total"],
        confidence_score=result["confidence_score"],
        recommended_side=result["recommended_side"],
    )

    db.add(prediction)
    db.commit()
    db.refresh(prediction)

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
        .filter(Game.game_date == today)
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
        }
        for r in rows
    ]
