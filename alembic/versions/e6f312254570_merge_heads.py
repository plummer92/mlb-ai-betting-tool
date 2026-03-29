"""merge heads

Revision ID: e6f312254570
Revises: 001_add_calibration_and_kbb, d2e3f4a5b6c7
Create Date: 2026-03-29 17:36:40.904151

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6f312254570'
down_revision: Union[str, Sequence[str], None] = ('001_add_calibration_and_kbb', 'd2e3f4a5b6c7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
