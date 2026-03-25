"""widen edge_results total_edge column

total_edge stores (model_total - book_total) which can exceed ±9.9999
when the model projects a very different run total from the book line.
Widen from NUMERIC(5,4) to NUMERIC(6,2).

Revision ID: e3a1f8b92c45
Revises: 602f4d267e02
Create Date: 2026-03-25
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'e3a1f8b92c45'
down_revision: Union[str, Sequence[str], None] = '602f4d267e02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE edge_results ALTER COLUMN total_edge TYPE NUMERIC(6,2)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE edge_results ALTER COLUMN total_edge TYPE NUMERIC(5,4)"
    )
