"""merge heads

Revision ID: 7a76ae76bfb7
Revises: 6ab120e71daf, a1b2c3d4e5f6
Create Date: 2026-03-28 21:42:50.263388

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a76ae76bfb7'
down_revision: Union[str, Sequence[str], None] = ('6ab120e71daf', 'a1b2c3d4e5f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
