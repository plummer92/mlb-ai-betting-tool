"""widen reliever workload note

Revision ID: e1b2c3d4e5f7
Revises: c2d3e4f5a6b7
Create Date: 2026-06-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1b2c3d4e5f7"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    op.alter_column(
        "reliever_workload",
        "note",
        existing_type=sa.String(length=10),
        type_=sa.String(length=64),
        existing_nullable=True,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    op.alter_column(
        "reliever_workload",
        "note",
        existing_type=sa.String(length=64),
        type_=sa.String(length=10),
        existing_nullable=True,
    )
