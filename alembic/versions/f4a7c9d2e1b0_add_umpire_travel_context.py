"""add umpire crew travel context

Revision ID: f4a7c9d2e1b0
Revises: b8c9d0e1f2a3
Create Date: 2026-05-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4a7c9d2e1b0"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("umpire_assignments_v4", sa.Column("official_type", sa.String(length=50), nullable=True))
    op.add_column("umpire_assignments_v4", sa.Column("venue", sa.String(length=100), nullable=True))
    op.add_column("umpire_assignments_v4", sa.Column("home_team_id", sa.Integer(), nullable=True))
    op.add_column("umpire_assignments_v4", sa.Column("game_date", sa.Date(), nullable=True))
    op.add_column("umpire_assignments_v4", sa.Column("travel_miles", sa.Float(), nullable=True))
    op.add_column("umpire_assignments_v4", sa.Column("rest_days", sa.Integer(), nullable=True))
    op.add_column("umpire_assignments_v4", sa.Column("timezone_shift", sa.Integer(), nullable=True))
    op.add_column("umpire_assignments_v4", sa.Column("travel_stress", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("umpire_assignments_v4", "travel_stress")
    op.drop_column("umpire_assignments_v4", "timezone_shift")
    op.drop_column("umpire_assignments_v4", "rest_days")
    op.drop_column("umpire_assignments_v4", "travel_miles")
    op.drop_column("umpire_assignments_v4", "game_date")
    op.drop_column("umpire_assignments_v4", "home_team_id")
    op.drop_column("umpire_assignments_v4", "venue")
    op.drop_column("umpire_assignments_v4", "official_type")
