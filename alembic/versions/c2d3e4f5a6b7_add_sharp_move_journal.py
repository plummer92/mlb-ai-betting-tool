"""add sharp move journal

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-05-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sharp_move_journal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("edge_result_id", sa.Integer(), nullable=True),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("matchup", sa.String(length=120), nullable=True),
        sa.Column("play", sa.String(length=20), nullable=False),
        sa.Column("open_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("move_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sportsbook", sa.String(length=50), nullable=True),
        sa.Column("open_line", sa.Numeric(8, 3), nullable=True),
        sa.Column("open_price", sa.Integer(), nullable=True),
        sa.Column("pregame_line", sa.Numeric(8, 3), nullable=True),
        sa.Column("pregame_price", sa.Integer(), nullable=True),
        sa.Column("latest_line", sa.Numeric(8, 3), nullable=True),
        sa.Column("latest_price", sa.Integer(), nullable=True),
        sa.Column("line_move", sa.Numeric(8, 4), nullable=True),
        sa.Column("price_move", sa.Numeric(8, 4), nullable=True),
        sa.Column("line_clv", sa.Numeric(8, 4), nullable=True),
        sa.Column("price_clv", sa.Numeric(8, 4), nullable=True),
        sa.Column("movement_bucket", sa.String(length=30), nullable=False, server_default="no_movement"),
        sa.Column("market_signal", sa.String(length=30), nullable=False, server_default="NO_MOVE"),
        sa.Column("model_recommended_play", sa.String(length=20), nullable=True),
        sa.Column("model_agreed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_edge_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("model_ev", sa.Numeric(8, 4), nullable=True),
        sa.Column("readiness", sa.String(length=30), nullable=True),
        sa.Column("snapshot_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["edge_result_id"], ["edge_results.id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.game_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_date", "game_id", "play", name="uq_sharp_move_game_play_date"),
    )
    op.create_index("ix_sharp_move_journal_captured_at", "sharp_move_journal", ["captured_at"], unique=False)
    op.create_index("ix_sharp_move_journal_game_date", "sharp_move_journal", ["game_date"], unique=False)
    op.create_index("ix_sharp_move_journal_game_id", "sharp_move_journal", ["game_id"], unique=False)
    op.create_index("ix_sharp_move_journal_market_signal", "sharp_move_journal", ["market_signal"], unique=False)
    op.create_index("ix_sharp_move_journal_model_agreed", "sharp_move_journal", ["model_agreed"], unique=False)
    op.create_index("ix_sharp_move_journal_move_observed_at", "sharp_move_journal", ["move_observed_at"], unique=False)
    op.create_index("ix_sharp_move_journal_movement_bucket", "sharp_move_journal", ["movement_bucket"], unique=False)
    op.create_index("ix_sharp_move_journal_play", "sharp_move_journal", ["play"], unique=False)
    op.create_index("ix_sharp_move_journal_readiness", "sharp_move_journal", ["readiness"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sharp_move_journal_readiness", table_name="sharp_move_journal")
    op.drop_index("ix_sharp_move_journal_play", table_name="sharp_move_journal")
    op.drop_index("ix_sharp_move_journal_movement_bucket", table_name="sharp_move_journal")
    op.drop_index("ix_sharp_move_journal_move_observed_at", table_name="sharp_move_journal")
    op.drop_index("ix_sharp_move_journal_model_agreed", table_name="sharp_move_journal")
    op.drop_index("ix_sharp_move_journal_market_signal", table_name="sharp_move_journal")
    op.drop_index("ix_sharp_move_journal_game_id", table_name="sharp_move_journal")
    op.drop_index("ix_sharp_move_journal_game_date", table_name="sharp_move_journal")
    op.drop_index("ix_sharp_move_journal_captured_at", table_name="sharp_move_journal")
    op.drop_table("sharp_move_journal")
