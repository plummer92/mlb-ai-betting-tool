"""sprint3_pitcher_movement_direction

Sprint-3 schema additions:
- games: away_pitcher_id, home_pitcher_id (already added by sprint-3 migration 602f, safe IF NOT EXISTS)
- edge_results: movement_direction VARCHAR(20)

Revision ID: b7c2d4e91a30
Revises: e3a1f8b92c45
Create Date: 2026-03-25
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'b7c2d4e91a30'
down_revision: Union[str, Sequence[str], None] = 'e3a1f8b92c45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Pitcher ID columns — added by 602f migration; guard with IF NOT EXISTS
    op.execute("ALTER TABLE games ADD COLUMN IF NOT EXISTS away_pitcher_id INTEGER")
    op.execute("ALTER TABLE games ADD COLUMN IF NOT EXISTS home_pitcher_id INTEGER")

    # Movement direction on edge results
    op.execute(
        "ALTER TABLE edge_results ADD COLUMN IF NOT EXISTS movement_direction VARCHAR(20)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE edge_results DROP COLUMN IF EXISTS movement_direction")
    op.execute("ALTER TABLE games DROP COLUMN IF EXISTS home_pitcher_id")
    op.execute("ALTER TABLE games DROP COLUMN IF EXISTS away_pitcher_id")
