"""add bullpen ERA columns to backtest_games

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-03-28
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE backtest_games ADD COLUMN IF NOT EXISTS home_bullpen_era FLOAT")
    op.execute("ALTER TABLE backtest_games ADD COLUMN IF NOT EXISTS away_bullpen_era FLOAT")


def downgrade() -> None:
    op.execute("ALTER TABLE backtest_games DROP COLUMN IF EXISTS home_bullpen_era")
    op.execute("ALTER TABLE backtest_games DROP COLUMN IF EXISTS away_bullpen_era")
