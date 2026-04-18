"""add prediction dashboard metrics

Revision ID: c9f4e8a1b2d3
Revises: bf391ef528c7
Create Date: 2026-04-18
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c9f4e8a1b2d3"
down_revision: Union[str, Sequence[str], None] = "bf391ef528c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE predictions ADD COLUMN IF NOT EXISTS kbb_adv DOUBLE PRECISION")
    op.execute("ALTER TABLE predictions ADD COLUMN IF NOT EXISTS park_factor_adv DOUBLE PRECISION")
    op.execute("ALTER TABLE predictions ADD COLUMN IF NOT EXISTS pythagorean_win_pct_adv DOUBLE PRECISION")


def downgrade() -> None:
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS pythagorean_win_pct_adv")
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS park_factor_adv")
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS kbb_adv")
