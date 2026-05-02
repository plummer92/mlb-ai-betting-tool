"""v0.5 add public_bias_edge column to sandbox_predictions_v4

Revision ID: d4e6f8a0b2c3
Revises: c3d5e7f9a1b2
Create Date: 2026-05-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e6f8a0b2c3"
down_revision: Union[str, Sequence[str], None] = "c3d5e7f9a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sandbox_predictions_v4",
        sa.Column("public_bias_edge", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sandbox_predictions_v4", "public_bias_edge")
