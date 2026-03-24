"""sprint3_backtest_tables

Adds pitcher_id columns to games table.
Creates backtest_games and backtest_results tables.

Revision ID: 602f4d267e02
Revises: a5be5c37c7f0
Create Date: 2026-03-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '602f4d267e02'
down_revision: Union[str, Sequence[str], None] = 'a5be5c37c7f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Pitcher ID columns on games table
    op.execute("ALTER TABLE games ADD COLUMN IF NOT EXISTS away_pitcher_id INTEGER")
    op.execute("ALTER TABLE games ADD COLUMN IF NOT EXISTS home_pitcher_id INTEGER")

    # Backtest games table
    op.execute("""
        CREATE TABLE IF NOT EXISTS backtest_games (
            game_id           INTEGER PRIMARY KEY,
            game_date         DATE    NOT NULL,
            season            INTEGER NOT NULL,
            home_team_id      INTEGER NOT NULL,
            away_team_id      INTEGER NOT NULL,
            home_team         VARCHAR NOT NULL,
            away_team         VARCHAR NOT NULL,
            venue             VARCHAR,
            home_score        INTEGER,
            away_score        INTEGER,
            home_win          BOOLEAN,
            home_starter_id   INTEGER,
            away_starter_id   INTEGER,
            home_starter_name VARCHAR,
            away_starter_name VARCHAR,
            home_starter_era  FLOAT,
            away_starter_era  FLOAT,
            home_team_era     FLOAT,
            away_team_era     FLOAT,
            home_team_ops     FLOAT,
            away_team_ops     FLOAT,
            home_team_whip    FLOAT,
            away_team_whip    FLOAT,
            home_win_pct      FLOAT,
            away_win_pct      FLOAT,
            home_run_diff     INTEGER,
            away_run_diff     INTEGER,
            collected_at      TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_backtest_games_season    ON backtest_games (season)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_backtest_games_game_date ON backtest_games (game_date)")

    # Backtest results table
    op.execute("""
        CREATE TABLE IF NOT EXISTS backtest_results (
            id                   SERIAL PRIMARY KEY,
            run_at               TIMESTAMPTZ DEFAULT now(),
            seasons              VARCHAR NOT NULL,
            n_games              INTEGER NOT NULL,
            accuracy             FLOAT   NOT NULL,
            cv_accuracy          FLOAT   NOT NULL,
            log_loss             FLOAT,
            coefficients_json    TEXT    NOT NULL,
            feature_ranks_json   TEXT    NOT NULL
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS backtest_results")
    op.execute("DROP TABLE IF EXISTS backtest_games")
    op.execute("ALTER TABLE games DROP COLUMN IF EXISTS away_pitcher_id")
    op.execute("ALTER TABLE games DROP COLUMN IF EXISTS home_pitcher_id")
