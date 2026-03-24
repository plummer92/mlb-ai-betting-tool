from datetime import datetime, date
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import Game
from app.models.pydantic_models import GameOut
from app.services.mlb_api import fetch_schedule_for_date

router = APIRouter(prefix="/api/games", tags=["games"])


@router.post("/sync-today")
def sync_today_games(db: Session = Depends(get_db)):
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    games = fetch_schedule_for_date(today)

    for g in games:
        existing = db.query(Game).filter(Game.game_id == g["game_id"]).first()

        if existing:
            existing.status = g["status"]
            existing.start_time = g["start_time"]
            existing.venue = g["venue"]
            existing.away_probable_pitcher = g["away_probable_pitcher"]
            existing.home_probable_pitcher = g["home_probable_pitcher"]
            existing.final_away_score = g["final_away_score"]
            existing.final_home_score = g["final_home_score"]
            # Update weather whenever it becomes available
            if g["weather_condition"] is not None:
                existing.weather_condition = g["weather_condition"]
                existing.weather_temp      = g["weather_temp"]
                existing.weather_wind      = g["weather_wind"]
                existing.weather_wind_mph  = g["weather_wind_mph"]
                existing.weather_wind_dir  = g["weather_wind_dir"]
        else:
            db.add(
                Game(
                    game_id=g["game_id"],
                    game_date=date.fromisoformat(g["game_date"]),
                    season=g["season"],
                    away_team=g["away_team"],
                    home_team=g["home_team"],
                    away_team_id=g["away_team_id"],
                    home_team_id=g["home_team_id"],
                    venue=g["venue"],
                    status=g["status"],
                    start_time=g["start_time"],
                    away_probable_pitcher=g["away_probable_pitcher"],
                    home_probable_pitcher=g["home_probable_pitcher"],
                    final_away_score=g["final_away_score"],
                    final_home_score=g["final_home_score"],
                    weather_condition=g["weather_condition"],
                    weather_temp     =g["weather_temp"],
                    weather_wind     =g["weather_wind"],
                    weather_wind_mph =g["weather_wind_mph"],
                    weather_wind_dir =g["weather_wind_dir"],
                )
            )

    db.commit()
    return {"message": "Today's games synced", "count": len(games)}


@router.get("/today", response_model=list[GameOut])
def get_today_games(db: Session = Depends(get_db)):
    today = datetime.now(ZoneInfo("America/New_York")).date()
    games = db.query(Game).filter(Game.game_date == today).all()
    return games


@router.get("/{game_id}", response_model=GameOut)
def get_game(game_id: int, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.game_id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return game
