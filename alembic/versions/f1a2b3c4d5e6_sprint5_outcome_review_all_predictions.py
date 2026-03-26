"""sprint5: grade all predictions, not just bet-alert rows

- edge_result_id: NOT NULL → nullable (reviews now created for all predictions)
- pre_game_synopsis: NOT NULL → nullable (no synopsis if no alert)
- Add projected_away_score, projected_home_score, total_correct columns
- Swap unique constraint from (game_id, prediction_id, edge_result_id)
  to (game_id, prediction_id) — one review per prediction regardless of
  whether an edge/alert was generated

Revision ID: f1a2b3c4d5e6
Revises: d3f1a9c82b55
Create Date: 2026-03-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'd3f1a9c82b55'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop old 3-way constraint
    op.execute(
        "ALTER TABLE game_outcomes_review DROP CONSTRAINT IF EXISTS uq_outcome_review_triplet"
    )

    # Deduplicate to at most one row per (game_id, prediction_id) before adding new constraint
    op.execute("""
        DELETE FROM game_outcomes_review
        WHERE id NOT IN (
            SELECT DISTINCT ON (game_id, prediction_id) id
            FROM game_outcomes_review
            ORDER BY game_id, prediction_id, created_at DESC NULLS LAST
        )
    """)

    # Add new 2-way unique constraint
    op.execute(
        "ALTER TABLE game_outcomes_review "
        "ADD CONSTRAINT uq_outcome_review_prediction UNIQUE (game_id, prediction_id)"
    )

    # Make edge_result_id nullable
    op.execute(
        "ALTER TABLE game_outcomes_review ALTER COLUMN edge_result_id DROP NOT NULL"
    )

    # Make pre_game_synopsis nullable
    op.execute(
        "ALTER TABLE game_outcomes_review ALTER COLUMN pre_game_synopsis DROP NOT NULL"
    )

    # Add new scoring columns
    op.execute(
        "ALTER TABLE game_outcomes_review ADD COLUMN IF NOT EXISTS projected_away_score NUMERIC(6,2)"
    )
    op.execute(
        "ALTER TABLE game_outcomes_review ADD COLUMN IF NOT EXISTS projected_home_score NUMERIC(6,2)"
    )
    op.execute(
        "ALTER TABLE game_outcomes_review ADD COLUMN IF NOT EXISTS total_correct BOOLEAN"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE game_outcomes_review DROP COLUMN IF EXISTS total_correct")
    op.execute("ALTER TABLE game_outcomes_review DROP COLUMN IF EXISTS projected_home_score")
    op.execute("ALTER TABLE game_outcomes_review DROP COLUMN IF EXISTS projected_away_score")
    op.execute(
        "ALTER TABLE game_outcomes_review ALTER COLUMN pre_game_synopsis SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE game_outcomes_review ALTER COLUMN edge_result_id SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE game_outcomes_review DROP CONSTRAINT IF EXISTS uq_outcome_review_prediction"
    )
    op.execute(
        "ALTER TABLE game_outcomes_review "
        "ADD CONSTRAINT uq_outcome_review_triplet UNIQUE (game_id, prediction_id, edge_result_id)"
    )
