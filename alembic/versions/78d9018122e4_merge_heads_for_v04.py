"""merge_heads_for_v04

Revision ID: 78d9018122e4
Revises: 9c4c4f8d2b10, b3f9c2a1d4e7
Create Date: 2026-04-14 03:03:13.167107

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '78d9018122e4'
down_revision: Union[str, Sequence[str], None] = ('9c4c4f8d2b10', 'b3f9c2a1d4e7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
