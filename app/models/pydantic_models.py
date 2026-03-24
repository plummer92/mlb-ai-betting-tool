from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel


class GameOut(BaseModel):
    game_id: int
    game_date: date
    season: int
    away_team: str
    home_team: str
    away_team_id: Optional[int] = None
    home_team_id: Optional[int] = None
    venue: Optional[str] = None
    status: Optional[str] = None
    start_time: Optional[str] = None
    away_probable_pitcher: Optional[str] = None
    home_probable_pitcher: Optional[str] = None
    final_away_score: Optional[int] = None
    final_home_score: Optional[int] = None

    class Config:
        from_attributes = True


class PredictionOut(BaseModel):
    prediction_id: int
    game_id: int
    model_version: str
    sim_count: int
    away_win_pct: float
    home_win_pct: float
    projected_away_score: float
    projected_home_score: float
    projected_total: float
    confidence_score: float
    recommended_side: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
