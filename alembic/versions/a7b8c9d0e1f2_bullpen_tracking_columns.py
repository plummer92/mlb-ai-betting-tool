"""Add innings_pitched/note to reliever_workload; add avg columns to manager_tendencies

Revision ID: a7b8c9d0e1f2
Revises: d4e6f8a0b2c3
Create Date: 2026-05-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "d4e6f8a0b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reliever_workload", sa.Column("innings_pitched", sa.Float(), nullable=True))
    op.add_column("reliever_workload", sa.Column("note", sa.String(10), nullable=True))
    op.add_column("manager_tendencies", sa.Column("avg_relievers_per_game", sa.Float(), nullable=True))
    op.add_column("manager_tendencies", sa.Column("avg_bullpen_pitches_per_game", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("reliever_workload", "innings_pitched")
    op.drop_column("reliever_workload", "note")
    op.drop_column("manager_tendencies", "avg_relievers_per_game")
    op.drop_column("manager_tendencies", "avg_bullpen_pitches_per_game")
