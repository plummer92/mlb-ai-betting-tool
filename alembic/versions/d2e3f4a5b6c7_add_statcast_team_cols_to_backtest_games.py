"""add Statcast team columns to backtest_games (Phase 3)

exit_velo, barrel_rate, hard_hit_rate, sprint_speed for home and away teams.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-03-28
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for col in (
        "home_exit_velo", "away_exit_velo",
        "home_barrel_rate", "away_barrel_rate",
        "home_hard_hit_rate", "away_hard_hit_rate",
        "home_sprint_speed", "away_sprint_speed",
    ):
        op.execute(f"ALTER TABLE backtest_games ADD COLUMN IF NOT EXISTS {col} FLOAT")


def downgrade() -> None:
    for col in (
        "away_sprint_speed", "home_sprint_speed",
        "away_hard_hit_rate", "home_hard_hit_rate",
        "away_barrel_rate", "home_barrel_rate",
        "away_exit_velo", "home_exit_velo",
    ):
        op.execute(f"ALTER TABLE backtest_games DROP COLUMN IF EXISTS {col}")
