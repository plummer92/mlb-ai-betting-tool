"""Add Platt calibration columns, brier_score, and starter K-BB fields

Revision ID: 001_add_calibration_and_kbb
Revises: None
Create Date: 2026-03-29

New columns:
- predictions.calibrated_home_win_pct / calibrated_away_win_pct
- backtest_results.brier_score / calibration_params_json
- backtest_games.home_starter_kbb / away_starter_kbb
"""
from alembic import op
import sqlalchemy as sa

revision = "001_add_calibration_and_kbb"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # predictions: Platt-calibrated win probabilities
    op.add_column("predictions", sa.Column("calibrated_home_win_pct", sa.Float(), nullable=True))
    op.add_column("predictions", sa.Column("calibrated_away_win_pct", sa.Float(), nullable=True))

    # backtest_results: Brier score + Platt calibration parameters
    op.add_column("backtest_results", sa.Column("brier_score", sa.Float(), nullable=True))
    op.add_column("backtest_results", sa.Column("calibration_params_json", sa.String(), nullable=True))

    # backtest_games: K-BB differential for each starter
    op.add_column("backtest_games", sa.Column("home_starter_kbb", sa.Float(), nullable=True))
    op.add_column("backtest_games", sa.Column("away_starter_kbb", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("backtest_games", "away_starter_kbb")
    op.drop_column("backtest_games", "home_starter_kbb")
    op.drop_column("backtest_results", "calibration_params_json")
    op.drop_column("backtest_results", "brier_score")
    op.drop_column("predictions", "calibrated_away_win_pct")
    op.drop_column("predictions", "calibrated_home_win_pct")
