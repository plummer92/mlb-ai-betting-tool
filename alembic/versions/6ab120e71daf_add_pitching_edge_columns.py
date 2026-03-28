"""add_pitching_edge_columns

Revision ID: 6ab120e71daf
Revises: cf44c5276ff3
Create Date: 2026-03-27 17:58:18.343110

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6ab120e71daf'
down_revision: Union[str, Sequence[str], None] = 'cf44c5276ff3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ERA and WHIP stored on each prediction so edge_service can compute
    # pitching_edge_score without re-fetching from the MLB Stats API.
    op.add_column('predictions', sa.Column('home_era',  sa.Float(), nullable=True))
    op.add_column('predictions', sa.Column('away_era',  sa.Float(), nullable=True))
    op.add_column('predictions', sa.Column('home_whip', sa.Float(), nullable=True))
    op.add_column('predictions', sa.Column('away_whip', sa.Float(), nullable=True))

    # Backtest-weighted pitching quality score for the recommended play side.
    op.add_column('edge_results', sa.Column('pitching_edge_score', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('edge_results', 'pitching_edge_score')
    op.drop_column('predictions', 'away_whip')
    op.drop_column('predictions', 'home_whip')
    op.drop_column('predictions', 'away_era')
    op.drop_column('predictions', 'home_era')
