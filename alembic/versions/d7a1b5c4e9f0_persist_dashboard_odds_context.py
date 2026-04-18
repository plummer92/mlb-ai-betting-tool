"""persist dashboard odds context

Revision ID: d7a1b5c4e9f0
Revises: c9f4e8a1b2d3
Create Date: 2026-04-18
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d7a1b5c4e9f0"
down_revision: Union[str, Sequence[str], None] = "c9f4e8a1b2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE edge_results ADD COLUMN IF NOT EXISTS sportsbook VARCHAR(50)")
    op.execute("ALTER TABLE edge_results ADD COLUMN IF NOT EXISTS odds_snapshot_type VARCHAR(20)")
    op.execute("ALTER TABLE edge_results ADD COLUMN IF NOT EXISTS away_ml INTEGER")
    op.execute("ALTER TABLE edge_results ADD COLUMN IF NOT EXISTS home_ml INTEGER")
    op.execute("ALTER TABLE edge_results ADD COLUMN IF NOT EXISTS over_odds INTEGER")
    op.execute("ALTER TABLE edge_results ADD COLUMN IF NOT EXISTS under_odds INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE edge_results DROP COLUMN IF EXISTS under_odds")
    op.execute("ALTER TABLE edge_results DROP COLUMN IF EXISTS over_odds")
    op.execute("ALTER TABLE edge_results DROP COLUMN IF EXISTS home_ml")
    op.execute("ALTER TABLE edge_results DROP COLUMN IF EXISTS away_ml")
    op.execute("ALTER TABLE edge_results DROP COLUMN IF EXISTS odds_snapshot_type")
    op.execute("ALTER TABLE edge_results DROP COLUMN IF EXISTS sportsbook")
