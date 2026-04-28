"""v0.5 add series position columns to sandbox_predictions_v4

Revision ID: c3d5e7f9a1b2
Revises: b2e4c6d8f0a1
Create Date: 2026-04-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d5e7f9a1b2"
down_revision: Union[str, Sequence[str], None] = "b2e4c6d8f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sandbox_predictions_v4",
        sa.Column("series_game_number", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sandbox_predictions_v4",
        sa.Column("is_series_opener", sa.Boolean(), nullable=True, server_default="false"),
    )
    op.add_column(
        "sandbox_predictions_v4",
        sa.Column("is_series_finale", sa.Boolean(), nullable=True, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("sandbox_predictions_v4", "is_series_finale")
    op.drop_column("sandbox_predictions_v4", "is_series_opener")
    op.drop_column("sandbox_predictions_v4", "series_game_number")
