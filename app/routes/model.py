import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import Game, Prediction
from app.models.pydantic_models import PredictionOut
from app.services.feature_builder import build_team_features, is_dome_venue, weather_run_modifier
from app.services.mlb_api import fetch_all_team_records, fetch_team_stats
from app.services.simulator import run_monte_carlo

router = APIRouter(prefix="/api/model", tags=["model"])


def _run_model_for_game(game: Game, team_records: dict, db: Session) -> Prediction:
    """Core logic shared by single-game and bulk endpoints."""
    away_raw = fetch_team_stats(team_id=game.away_team_id, season=game.season)
    home_raw = fetch_team_stats(team_id=game.home_team_id, season=game.season)

    away_record = team_records.get(game.away_team_id, {"wins": 0, "losses": 0})
    home_record = team_records.get(game.home_team_id, {"wins": 0, "losses": 0})

    away_features = build_team_features(away_raw, wins=away_record["wins"], losses=away_record["losses"])
    home_features = build_team_features(home_raw, wins=home_record["wins"], losses=home_record["losses"])

    weather_mod = weather_run_modifier(
        temp=game.weather_temp,
        wind_mph=game.weather_wind_mph,
        wind_dir=game.weather_wind_dir,
        is_dome=is_dome_venue(game.venue),
    )

    result = run_monte_carlo(
        away_team=away_features,
        home_team=home_features,
        sim_count=1000,
        weather_modifier=weather_mod,
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
        sim_totals_json=json.dumps(result["sim_totals"]),
    )

    db.add(prediction)
    db.commit()
    db.refresh(prediction)
    return prediction


@router.post("/run/{game_id}", response_model=PredictionOut)
def run_model(game_id: int, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.game_id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    team_records = fetch_all_team_records(season=game.season)
    return _run_model_for_game(game, team_records, db)


@router.post("/run/all-today")
def run_all_today(db: Session = Depends(get_db)):
    """Run Monte Carlo for every game today in one call. Returns a summary."""
    today = datetime.now(ZoneInfo("America/New_York")).date()
    games = db.query(Game).filter(Game.game_date == today).all()

    if not games:
        return {"ran": 0, "skipped": 0, "results": []}

    # Fetch standings once — shared across all games
    season = games[0].season
    team_records = fetch_all_team_records(season=season)

    ran, skipped = [], []
    for game in games:
        if not game.away_team_id or not game.home_team_id:
            skipped.append(game.game_id)
            continue
        try:
            prediction = _run_model_for_game(game, team_records, db)
            ran.append({
                "game_id": game.game_id,
                "matchup": f"{game.away_team} @ {game.home_team}",
                "away_win_pct": prediction.away_win_pct,
                "home_win_pct": prediction.home_win_pct,
                "projected_total": prediction.projected_total,
                "weather_modifier": weather_run_modifier(
                    game.weather_temp, game.weather_wind_mph,
                    game.weather_wind_dir, is_dome_venue(game.venue),
                ),
            })
        except Exception as exc:
            skipped.append({"game_id": game.game_id, "error": str(exc)})

    return {"ran": len(ran), "skipped": len(skipped), "results": ran}
