from datetime import date, datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import Game
from app.models.pydantic_models import GameOut
from app.services.mlb_api import fetch_schedule_for_date

router = APIRouter(prefix="/api/games", tags=["games"])
ET = ZoneInfo("America/New_York")


@router.post("/sync-today")
def sync_today_games(db: Session = Depends(get_db)):
    today = datetime.now(ET).date().isoformat()
    games = fetch_schedule_for_date(today)

    for g in games:
        existing = db.query(Game).filter(Game.game_id == g["game_id"]).first()

        if existing:
            existing.status = g["status"]
            existing.start_time = g["start_time"]
            existing.venue = g["venue"]
            existing.away_probable_pitcher = g["away_probable_pitcher"]
            existing.away_pitcher_id = g["away_pitcher_id"]
            existing.home_probable_pitcher = g["home_probable_pitcher"]
            existing.home_pitcher_id = g["home_pitcher_id"]
            existing.final_away_score = g["final_away_score"]
            existing.final_home_score = g["final_home_score"]
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
                    away_pitcher_id=g["away_pitcher_id"],
                    home_probable_pitcher=g["home_probable_pitcher"],
                    home_pitcher_id=g["home_pitcher_id"],
                    final_away_score=g["final_away_score"],
                    final_home_score=g["final_home_score"],
                )
            )

    db.commit()
    return {"message": "Today's games synced", "count": len(games)}


@router.get("/today", response_model=list[GameOut])
def get_today_games(db: Session = Depends(get_db)):
    today = datetime.now(ET).date()
    games = db.query(Game).filter(Game.game_date == today).all()
    return games


@router.get("/{game_id}", response_model=GameOut)
def get_game(game_id: int, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.game_id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return game
