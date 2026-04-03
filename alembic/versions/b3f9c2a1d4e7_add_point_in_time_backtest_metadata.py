"""add point-in-time backtest metadata

Revision ID: b3f9c2a1d4e7
Revises: e6f312254570
Create Date: 2026-04-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3f9c2a1d4e7"
down_revision: Union[str, Sequence[str], None] = "e6f312254570"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("backtest_games", sa.Column("game_start_time", sa.DateTime(timezone=True), nullable=True))
    op.add_column("backtest_games", sa.Column("feature_cutoff_time", sa.DateTime(timezone=True), nullable=True))
    op.add_column("backtest_games", sa.Column("feature_cutoff_policy", sa.String(), nullable=True))
    op.add_column("backtest_games", sa.Column("home_games_played", sa.Integer(), nullable=True))
    op.add_column("backtest_games", sa.Column("away_games_played", sa.Integer(), nullable=True))
    op.add_column("backtest_games", sa.Column("home_pythagorean_win_pct", sa.Float(), nullable=True))
    op.add_column("backtest_games", sa.Column("away_pythagorean_win_pct", sa.Float(), nullable=True))
    op.add_column("backtest_games", sa.Column("home_starter_whip", sa.Float(), nullable=True))
    op.add_column("backtest_games", sa.Column("away_starter_whip", sa.Float(), nullable=True))
    op.add_column("backtest_games", sa.Column("home_starter_starts", sa.Integer(), nullable=True))
    op.add_column("backtest_games", sa.Column("away_starter_starts", sa.Integer(), nullable=True))
    op.add_column("backtest_games", sa.Column("odds_snapshot_type", sa.String(), nullable=True))
    op.add_column("backtest_games", sa.Column("odds_snapshot_policy", sa.String(), nullable=True))
    op.add_column("backtest_games", sa.Column("odds_row_id", sa.Integer(), nullable=True))
    op.add_column("backtest_games", sa.Column("odds_fetched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("backtest_games", sa.Column("odds_away_ml", sa.Integer(), nullable=True))
    op.add_column("backtest_games", sa.Column("odds_home_ml", sa.Integer(), nullable=True))
    op.add_column("backtest_games", sa.Column("odds_total", sa.Numeric(4, 1), nullable=True))
    op.add_column("backtest_games", sa.Column("features_complete", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("backtest_games", sa.Column("odds_complete", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("backtest_games", sa.Column("incomplete_reasons_json", sa.Text(), nullable=True))
    op.alter_column("backtest_games", "features_complete", server_default=None)
    op.alter_column("backtest_games", "odds_complete", server_default=None)

    op.add_column("backtest_results", sa.Column("dataset_summary_json", sa.Text(), nullable=True))
    op.add_column("backtest_results", sa.Column("validation_summary_json", sa.Text(), nullable=True))
    op.add_column("backtest_results", sa.Column("limitations_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("backtest_results", "limitations_json")
    op.drop_column("backtest_results", "validation_summary_json")
    op.drop_column("backtest_results", "dataset_summary_json")

    op.drop_column("backtest_games", "incomplete_reasons_json")
    op.drop_column("backtest_games", "odds_complete")
    op.drop_column("backtest_games", "features_complete")
    op.drop_column("backtest_games", "odds_total")
    op.drop_column("backtest_games", "odds_home_ml")
    op.drop_column("backtest_games", "odds_away_ml")
    op.drop_column("backtest_games", "odds_fetched_at")
    op.drop_column("backtest_games", "odds_row_id")
    op.drop_column("backtest_games", "odds_snapshot_policy")
    op.drop_column("backtest_games", "odds_snapshot_type")
    op.drop_column("backtest_games", "away_starter_starts")
    op.drop_column("backtest_games", "home_starter_starts")
    op.drop_column("backtest_games", "away_starter_whip")
    op.drop_column("backtest_games", "home_starter_whip")
    op.drop_column("backtest_games", "away_pythagorean_win_pct")
    op.drop_column("backtest_games", "home_pythagorean_win_pct")
    op.drop_column("backtest_games", "away_games_played")
    op.drop_column("backtest_games", "home_games_played")
    op.drop_column("backtest_games", "feature_cutoff_policy")
    op.drop_column("backtest_games", "feature_cutoff_time")
    op.drop_column("backtest_games", "game_start_time")
