"""add xera columns to predictions

Revision ID: c1d2e3f4a5b6
Revises: 7a76ae76bfb7
Create Date: 2026-03-28
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = '7a76ae76bfb7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE predictions ADD COLUMN IF NOT EXISTS home_starter_xera FLOAT")
    op.execute("ALTER TABLE predictions ADD COLUMN IF NOT EXISTS away_starter_xera FLOAT")
    op.execute("ALTER TABLE predictions ADD COLUMN IF NOT EXISTS using_xera BOOLEAN NOT NULL DEFAULT FALSE")


def downgrade() -> None:
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS using_xera")
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS away_starter_xera")
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS home_starter_xera")
