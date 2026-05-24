"""add tradable decision snapshots

Revision ID: b1c2d3e4f5a6
Revises: b0c1d2e3f4a5
Create Date: 2026-05-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "b0c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tradable_decision_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("edge_result_id", sa.Integer(), nullable=True),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("matchup", sa.String(length=120), nullable=True),
        sa.Column("play", sa.String(length=20), nullable=True),
        sa.Column("decision_status", sa.String(length=20), nullable=False),
        sa.Column("tradable_signal", sa.String(length=20), nullable=False),
        sa.Column("tradable_reason", sa.Text(), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("raw_edge_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("adjusted_edge_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("market_respect_score", sa.Integer(), nullable=True),
        sa.Column("market_respect_tag", sa.String(length=50), nullable=True),
        sa.Column("odds_freshness_status", sa.String(length=30), nullable=True),
        sa.Column("policy_status", sa.String(length=30), nullable=True),
        sa.Column("policy_score", sa.Integer(), nullable=True),
        sa.Column("trade_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("snapshot_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["edge_result_id"], ["edge_results.id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.game_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_date", "game_id", "edge_result_id", name="uq_tradable_decision_game_edge_date"),
    )
    op.create_index("ix_tradable_decision_snapshots_captured_at", "tradable_decision_snapshots", ["captured_at"], unique=False)
    op.create_index("ix_tradable_decision_snapshots_decision_status", "tradable_decision_snapshots", ["decision_status"], unique=False)
    op.create_index("ix_tradable_decision_snapshots_game_date", "tradable_decision_snapshots", ["game_date"], unique=False)
    op.create_index("ix_tradable_decision_snapshots_game_id", "tradable_decision_snapshots", ["game_id"], unique=False)
    op.create_index("ix_tradable_decision_snapshots_play", "tradable_decision_snapshots", ["play"], unique=False)
    op.create_index("ix_tradable_decision_snapshots_tradable_signal", "tradable_decision_snapshots", ["tradable_signal"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tradable_decision_snapshots_tradable_signal", table_name="tradable_decision_snapshots")
    op.drop_index("ix_tradable_decision_snapshots_play", table_name="tradable_decision_snapshots")
    op.drop_index("ix_tradable_decision_snapshots_game_id", table_name="tradable_decision_snapshots")
    op.drop_index("ix_tradable_decision_snapshots_game_date", table_name="tradable_decision_snapshots")
    op.drop_index("ix_tradable_decision_snapshots_decision_status", table_name="tradable_decision_snapshots")
    op.drop_index("ix_tradable_decision_snapshots_captured_at", table_name="tradable_decision_snapshots")
    op.drop_table("tradable_decision_snapshots")
