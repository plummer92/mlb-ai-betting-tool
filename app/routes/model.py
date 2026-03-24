from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import Game, Prediction
from app.models.pydantic_models import PredictionOut
from app.services.feature_builder import build_team_features
from app.services.mlb_api import fetch_team_stats
from app.services.simulator import run_monte_carlo

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
        model_version="v0.1-neon",
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
