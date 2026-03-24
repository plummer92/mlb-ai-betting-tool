import enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Date,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
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


class SnapshotType(str, enum.Enum):
    open = "open"
    pregame = "pregame"
    live = "live"


class GameOdds(Base):
    __tablename__ = "game_odds"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False, index=True)
    sportsbook = Column(String(50), nullable=False)
    snapshot_type = Column(Enum(SnapshotType), nullable=False, default=SnapshotType.open)
    fetched_at = Column(DateTime(timezone=True), default=func.now())

    # Moneyline
    away_ml = Column(Integer)
    home_ml = Column(Integer)

    # Totals
    total_line = Column(Numeric(4, 1))
    over_odds = Column(Integer)
    under_odds = Column(Integer)

    # Runline
    runline_away = Column(Numeric(3, 1))
    runline_odds = Column(Integer)

    __table_args__ = (
        UniqueConstraint("game_id", "sportsbook", "snapshot_type", name="uq_odds_per_snapshot_type"),
    )


class LineMovement(Base):
    __tablename__ = "line_movement"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False, unique=True)
    sportsbook = Column(String(50), nullable=False)
    calculated_at = Column(DateTime(timezone=True), default=func.now())

    open_away_ml = Column(Integer)
    open_home_ml = Column(Integer)
    open_total = Column(Numeric(4, 1))

    pregame_away_ml = Column(Integer)
    pregame_home_ml = Column(Integer)
    pregame_total = Column(Numeric(4, 1))

    # Movement deltas (pregame - open, in implied probability points)
    away_prob_move = Column(Numeric(5, 4))
    home_prob_move = Column(Numeric(5, 4))
    total_move = Column(Numeric(4, 1))

    sharp_away = Column(Boolean, default=False)
    sharp_home = Column(Boolean, default=False)
    total_steam_over = Column(Boolean, default=False)
    total_steam_under = Column(Boolean, default=False)


class EdgeResult(Base):
    __tablename__ = "edge_results"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False, index=True)
    prediction_id = Column(Integer, ForeignKey("predictions.prediction_id"), nullable=False)
    odds_id = Column(Integer, ForeignKey("game_odds.id"), nullable=False)
    movement_id = Column(Integer, ForeignKey("line_movement.id"), nullable=True)
    calculated_at = Column(DateTime(timezone=True), default=func.now())

    model_away_win_pct = Column(Numeric(5, 4))
    model_home_win_pct = Column(Numeric(5, 4))
    implied_away_pct = Column(Numeric(5, 4))
    implied_home_pct = Column(Numeric(5, 4))

    edge_away = Column(Numeric(5, 4))
    edge_home = Column(Numeric(5, 4))

    ev_away = Column(Numeric(6, 4))
    ev_home = Column(Numeric(6, 4))

    movement_boost = Column(Numeric(5, 4), default=0)

    model_total = Column(Numeric(4, 1))
    book_total = Column(Numeric(4, 1))
    total_edge = Column(Numeric(5, 4))
    ev_over = Column(Numeric(6, 4))
    ev_under = Column(Numeric(6, 4))

    recommended_play = Column(String(20))
    confidence_tier = Column(String(10))
    edge_pct = Column(Numeric(5, 4))
