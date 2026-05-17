"""add play-by-play comparison memory

Revision ID: a9d4e6f2c8b1
Revises: f4a7c9d2e1b0
Create Date: 2026-05-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9d4e6f2c8b1"
down_revision: Union[str, Sequence[str], None] = "f4a7c9d2e1b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "playbyplay_comparisons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(length=40), nullable=False),
        sa.Column("projection_bucket", sa.String(length=20), nullable=False),
        sa.Column("projected_total", sa.Float(), nullable=True),
        sa.Column("simulated_total", sa.Integer(), nullable=False),
        sa.Column("actual_total", sa.Integer(), nullable=False),
        sa.Column("run_delta", sa.Integer(), nullable=False),
        sa.Column("home_run_delta", sa.Integer(), nullable=False),
        sa.Column("walk_delta", sa.Integer(), nullable=False),
        sa.Column("strikeout_delta", sa.Integer(), nullable=False),
        sa.Column("sim_summary_json", sa.Text(), nullable=False),
        sa.Column("actual_summary_json", sa.Text(), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=True),
        sa.Column("lessons_json", sa.Text(), nullable=True),
        sa.Column("compared_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.game_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", name="uq_playbyplay_comparison_game"),
    )
    op.create_index("ix_playbyplay_comparisons_game_id", "playbyplay_comparisons", ["game_id"], unique=False)
    op.create_index("ix_playbyplay_comparisons_game_date", "playbyplay_comparisons", ["game_date"], unique=False)
    op.create_index("ix_playbyplay_comparisons_projection_bucket", "playbyplay_comparisons", ["projection_bucket"], unique=False)
    op.create_index("ix_playbyplay_comparisons_season", "playbyplay_comparisons", ["season"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_playbyplay_comparisons_season", table_name="playbyplay_comparisons")
    op.drop_index("ix_playbyplay_comparisons_projection_bucket", table_name="playbyplay_comparisons")
    op.drop_index("ix_playbyplay_comparisons_game_date", table_name="playbyplay_comparisons")
    op.drop_index("ix_playbyplay_comparisons_game_id", table_name="playbyplay_comparisons")
    op.drop_table("playbyplay_comparisons")
