"""v0.5 add travel stress columns to sandbox_predictions_v4

Revision ID: a1f3b7c2e5d8
Revises: d7a1b5c4e9f0
Create Date: 2026-04-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1f3b7c2e5d8"
down_revision: Union[str, Sequence[str], None] = "d7a1b5c4e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sandbox_predictions_v4",
        sa.Column("travel_stress_home", sa.Float(), nullable=True),
    )
    op.add_column(
        "sandbox_predictions_v4",
        sa.Column("travel_stress_away", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sandbox_predictions_v4", "travel_stress_away")
    op.drop_column("sandbox_predictions_v4", "travel_stress_home")
