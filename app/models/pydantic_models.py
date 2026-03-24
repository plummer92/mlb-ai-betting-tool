from datetime import date, datetime
from decimal import Decimal
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

    weather_condition: Optional[str] = None
    weather_temp:      Optional[int] = None
    weather_wind:      Optional[str] = None
    weather_wind_mph:  Optional[int] = None
    weather_wind_dir:  Optional[str] = None

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


class EdgeResultOut(BaseModel):
    id: int
    game_id: int
    prediction_id: int
    odds_id: int
    movement_id: Optional[int] = None
    calculated_at: Optional[datetime] = None

    model_away_win_pct: Optional[Decimal] = None
    model_home_win_pct: Optional[Decimal] = None
    implied_away_pct: Optional[Decimal] = None
    implied_home_pct: Optional[Decimal] = None

    edge_away: Optional[Decimal] = None
    edge_home: Optional[Decimal] = None
    ev_away: Optional[Decimal] = None
    ev_home: Optional[Decimal] = None
    movement_boost: Optional[Decimal] = None

    model_total: Optional[Decimal] = None
    book_total: Optional[Decimal] = None
    total_edge: Optional[Decimal] = None
    ev_over: Optional[Decimal] = None
    ev_under: Optional[Decimal] = None

    recommended_play: Optional[str] = None
    confidence_tier: Optional[str] = None
    edge_pct: Optional[Decimal] = None

    class Config:
        from_attributes = True
