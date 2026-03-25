"""sprint4_edge_results_unique_constraint

Add UNIQUE(game_id, prediction_id) to edge_results, deduplicating stale rows
first by keeping only the latest record per pair.

Revision ID: d3f1a9c82b55
Revises: 602f4d267e02
Create Date: 2026-03-25
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd3f1a9c82b55'
down_revision: Union[str, Sequence[str], None] = 'b7c2d4e91a30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove duplicate rows, keeping the latest calculated_at per (game_id, prediction_id)
    op.execute("""
        DELETE FROM edge_results
        WHERE id NOT IN (
            SELECT DISTINCT ON (game_id, prediction_id) id
            FROM edge_results
            ORDER BY game_id, prediction_id, calculated_at DESC NULLS LAST
        )
    """)

    op.execute("""
        ALTER TABLE edge_results
        ADD CONSTRAINT uq_edge_game_prediction
        UNIQUE (game_id, prediction_id)
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE edge_results DROP CONSTRAINT IF EXISTS uq_edge_game_prediction")
