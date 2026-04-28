"""v0.5 add weather/wind columns to sandbox_predictions_v4

Revision ID: b2e4c6d8f0a1
Revises: a1f3b7c2e5d8
Create Date: 2026-04-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2e4c6d8f0a1"
down_revision: Union[str, Sequence[str], None] = "a1f3b7c2e5d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sandbox_predictions_v4",
        sa.Column("wind_factor", sa.Float(), nullable=True),
    )
    op.add_column(
        "sandbox_predictions_v4",
        sa.Column("temp_f", sa.Float(), nullable=True),
    )
    op.add_column(
        "sandbox_predictions_v4",
        sa.Column("humidity_pct", sa.Float(), nullable=True),
    )
    op.add_column(
        "sandbox_predictions_v4",
        sa.Column("is_dome", sa.Boolean(), nullable=True, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("sandbox_predictions_v4", "is_dome")
    op.drop_column("sandbox_predictions_v4", "humidity_pct")
    op.drop_column("sandbox_predictions_v4", "temp_f")
    op.drop_column("sandbox_predictions_v4", "wind_factor")
