"""v04_sandbox_pipeline_tables

Revision ID: bf391ef528c7
Revises: 78d9018122e4
Create Date: 2026-04-14 03:03:54.423437

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'bf391ef528c7'
down_revision: Union[str, Sequence[str], None] = '78d9018122e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create v0.4 sandbox pipeline tables."""
    op.create_table(
        'manager_tendencies',
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('manager_name', sa.String(length=100), nullable=True),
        sa.Column('b2b_usage_rate', sa.Float(), nullable=True),
        sa.Column('strict_pitch_cap', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('team_id'),
    )
    op.create_table(
        'reliever_workload',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=True),
        sa.Column('team_id', sa.Integer(), nullable=True),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('pitches_thrown', sa.Integer(), nullable=True),
        sa.Column('days_rest', sa.Integer(), nullable=True),
        sa.Column('appearances_last_3_days', sa.Integer(), nullable=True),
        sa.Column('player_name', sa.String(length=100), nullable=True),
        sa.Column('collected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('player_id', 'date', name='uq_reliever_workload_player_date'),
    )
    op.create_table(
        'f5_lines_v4',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('game_id', sa.Integer(), nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('f5_over_under_line', sa.Float(), nullable=True),
        sa.Column('f5_over_odds', sa.Integer(), nullable=True),
        sa.Column('f5_under_odds', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['game_id'], ['games.game_id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'umpire_assignments_v4',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('game_id', sa.Integer(), nullable=True),
        sa.Column('umpire_name', sa.String(length=100), nullable=False),
        sa.Column('historical_k_rate_delta', sa.Float(), nullable=True),
        sa.Column('run_expectancy_impact', sa.Float(), nullable=True),
        sa.Column('season', sa.Integer(), nullable=True),
        sa.Column('collected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['game_id'], ['games.game_id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'sandbox_predictions_v4',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('game_id', sa.Integer(), nullable=True),
        sa.Column('game_date', sa.Date(), nullable=True),
        sa.Column('season', sa.Integer(), nullable=True),
        sa.Column('away_team', sa.String(length=100), nullable=True),
        sa.Column('home_team', sa.String(length=100), nullable=True),
        sa.Column('f5_projected_total', sa.Float(), nullable=True),
        sa.Column('f5_line', sa.Float(), nullable=True),
        sa.Column('f5_pick', sa.String(length=10), nullable=True),
        sa.Column('f5_edge_pct', sa.Float(), nullable=True),
        sa.Column('umpire_name', sa.String(length=100), nullable=True),
        sa.Column('umpire_run_impact', sa.Float(), nullable=True),
        sa.Column('home_bullpen_strength', sa.Float(), nullable=True),
        sa.Column('away_bullpen_strength', sa.Float(), nullable=True),
        sa.Column('bullpen_convergence', sa.Boolean(), nullable=True),
        sa.Column('full_game_projected_total', sa.Float(), nullable=True),
        sa.Column('v3_projected_total', sa.Float(), nullable=True),
        sa.Column('v3_home_win_pct', sa.Float(), nullable=True),
        sa.Column('v4_home_win_pct', sa.Float(), nullable=True),
        sa.Column('v4_confidence', sa.Float(), nullable=True),
        sa.Column('v3_v4_agreement', sa.Boolean(), nullable=True),
        sa.Column('f5_result', sa.String(length=10), nullable=True),
        sa.Column('full_game_result', sa.String(length=10), nullable=True),
        sa.Column('f5_graded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('full_game_graded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['game_id'], ['games.game_id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Drop v0.4 sandbox pipeline tables."""
    op.drop_table('sandbox_predictions_v4')
    op.drop_table('umpire_assignments_v4')
    op.drop_table('f5_lines_v4')
    op.drop_table('reliever_workload')
    op.drop_table('manager_tendencies')
