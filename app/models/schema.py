from sqlalchemy import Column, Integer, String, Float, Date, DateTime
from sqlalchemy.sql import func

from app.db import Base


class Game(Base):
    __tablename__ = "games"

    game_id = Column(Integer, primary_key=True, index=True)
    game_date = Column(Date, nullable=False, index=True)
    season = Column(Integer, nullable=False, index=True)

    away_team = Column(String, nullable=False)
    home_team = Column(String, nullable=False)

    away_team_id = Column(Integer, nullable=True)
    home_team_id = Column(Integer, nullable=True)

    venue = Column(String, nullable=True)
    status = Column(String, nullable=True)
    start_time = Column(String, nullable=True)

    away_probable_pitcher = Column(String, nullable=True)
    home_probable_pitcher = Column(String, nullable=True)

    final_away_score = Column(Integer, nullable=True)
    final_home_score = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Prediction(Base):
    __tablename__ = "predictions"

    prediction_id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, nullable=False, index=True)

    model_version = Column(String, nullable=False, default="v0.1")
    sim_count = Column(Integer, nullable=False, default=1000)

    away_win_pct = Column(Float, nullable=False)
    home_win_pct = Column(Float, nullable=False)

    projected_away_score = Column(Float, nullable=False)
    projected_home_score = Column(Float, nullable=False)
    projected_total = Column(Float, nullable=False)

    confidence_score = Column(Float, nullable=False)
    recommended_side = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
