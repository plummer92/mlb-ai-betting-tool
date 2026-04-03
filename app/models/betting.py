"""
Betting execution models — isolated from prediction/odds pipeline.
Only written to by the execution service; never by data ingestion.
"""
import enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.sql import func

from app.db import Base


class BetOrderStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    placed_paper = "placed_paper"
    placed = "placed"
    settled = "settled"
    cancelled = "cancelled"
    failed = "failed"


class BetOrder(Base):
    __tablename__ = "bet_orders"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False, index=True)
    sportsbook = Column(String(50), nullable=False)
    provider_mode = Column(String(10), nullable=False)  # paper | live

    market_type = Column(String(20), nullable=False)   # moneyline | total | spread
    side = Column(String(20), nullable=False)           # away_ml | home_ml | over | under

    requested_line = Column(Numeric(5, 1), nullable=True)
    requested_odds = Column(Integer, nullable=True)
    requested_stake = Column(Numeric(10, 2), nullable=False)

    edge_pct = Column(Numeric(5, 4), nullable=True)
    ev = Column(Numeric(6, 4), nullable=True)
    confidence = Column(String(10), nullable=True)
    source_rank = Column(Integer, nullable=True)

    status = Column(String(20), nullable=False, default="pending")
    rejection_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BetExecution(Base):
    __tablename__ = "bet_executions"

    id = Column(Integer, primary_key=True)
    bet_order_id = Column(Integer, ForeignKey("bet_orders.id"), nullable=False, index=True)
    external_bet_id = Column(String(100), nullable=True)

    placed_line = Column(Numeric(5, 1), nullable=True)
    placed_odds = Column(Integer, nullable=True)
    placed_stake = Column(Numeric(10, 2), nullable=True)
    placed_at = Column(DateTime(timezone=True), nullable=True)
    fill_status = Column(String(20), nullable=True)  # filled | partial | rejected
    raw_response_json = Column(Text, nullable=True)

    settled_result = Column(String(10), nullable=True)   # win | loss | push
    profit_loss_units = Column(Numeric(10, 4), nullable=True)
    profit_loss_dollars = Column(Numeric(10, 2), nullable=True)
    settled_at = Column(DateTime(timezone=True), nullable=True)


class BankrollSnapshot(Base):
    __tablename__ = "bankroll_snapshots"

    id = Column(Integer, primary_key=True)
    provider_mode = Column(String(10), nullable=False)
    sportsbook = Column(String(50), nullable=False)
    bankroll = Column(Numeric(12, 2), nullable=False)
    available_balance = Column(Numeric(12, 2), nullable=False)
    captured_at = Column(DateTime(timezone=True), server_default=func.now())
