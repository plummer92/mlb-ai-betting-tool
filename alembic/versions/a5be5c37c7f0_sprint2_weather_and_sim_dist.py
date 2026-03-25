"""sprint2_weather_and_sim_dist

Creates all tables (IF NOT EXISTS) and adds sprint-2 columns:
- games: weather_condition, weather_temp, weather_wind, weather_wind_mph, weather_wind_dir
- predictions: sim_totals_json

Safe to run against an existing database.

Revision ID: a5be5c37c7f0
Revises:
Create Date: 2026-03-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a5be5c37c7f0'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Ensure all tables exist ──────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS games (
            game_id      INTEGER PRIMARY KEY,
            game_date    DATE    NOT NULL,
            season       INTEGER NOT NULL,
            away_team    VARCHAR NOT NULL,
            home_team    VARCHAR NOT NULL,
            away_team_id INTEGER,
            home_team_id INTEGER,
            venue        VARCHAR,
            status       VARCHAR,
            start_time   VARCHAR,
            away_probable_pitcher VARCHAR,
            home_probable_pitcher VARCHAR,
            final_away_score INTEGER,
            final_home_score INTEGER,
            created_at   TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            prediction_id SERIAL PRIMARY KEY,
            game_id       INTEGER NOT NULL,
            model_version VARCHAR NOT NULL DEFAULT 'v0.1',
            sim_count     INTEGER NOT NULL DEFAULT 1000,
            away_win_pct  FLOAT   NOT NULL,
            home_win_pct  FLOAT   NOT NULL,
            projected_away_score FLOAT NOT NULL,
            projected_home_score FLOAT NOT NULL,
            projected_total      FLOAT NOT NULL,
            confidence_score     FLOAT NOT NULL,
            recommended_side     VARCHAR,
            created_at    TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS game_odds (
            id            SERIAL PRIMARY KEY,
            game_id       INTEGER NOT NULL REFERENCES games(game_id),
            sportsbook    VARCHAR(50) NOT NULL,
            snapshot_type VARCHAR NOT NULL DEFAULT 'open',
            fetched_at    TIMESTAMPTZ DEFAULT now(),
            away_ml       INTEGER,
            home_ml       INTEGER,
            total_line    NUMERIC(4,1),
            over_odds     INTEGER,
            under_odds    INTEGER,
            runline_away  NUMERIC(3,1),
            runline_odds  INTEGER,
            CONSTRAINT uq_odds_per_snapshot_type UNIQUE (game_id, sportsbook, snapshot_type)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS line_movement (
            id            SERIAL PRIMARY KEY,
            game_id       INTEGER NOT NULL UNIQUE REFERENCES games(game_id),
            sportsbook    VARCHAR(50) NOT NULL,
            calculated_at TIMESTAMPTZ DEFAULT now(),
            open_away_ml  INTEGER,
            open_home_ml  INTEGER,
            open_total    NUMERIC(4,1),
            pregame_away_ml INTEGER,
            pregame_home_ml INTEGER,
            pregame_total   NUMERIC(4,1),
            away_prob_move  NUMERIC(5,4),
            home_prob_move  NUMERIC(5,4),
            total_move      NUMERIC(4,1),
            sharp_away      BOOLEAN DEFAULT FALSE,
            sharp_home      BOOLEAN DEFAULT FALSE,
            total_steam_over  BOOLEAN DEFAULT FALSE,
            total_steam_under BOOLEAN DEFAULT FALSE
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS edge_results (
            id              SERIAL PRIMARY KEY,
            game_id         INTEGER NOT NULL REFERENCES games(game_id),
            prediction_id   INTEGER NOT NULL REFERENCES predictions(prediction_id),
            odds_id         INTEGER NOT NULL REFERENCES game_odds(id),
            movement_id     INTEGER REFERENCES line_movement(id),
            calculated_at   TIMESTAMPTZ DEFAULT now(),
            model_away_win_pct NUMERIC(5,4),
            model_home_win_pct NUMERIC(5,4),
            implied_away_pct   NUMERIC(5,4),
            implied_home_pct   NUMERIC(5,4),
            edge_away   NUMERIC(5,4),
            edge_home   NUMERIC(5,4),
            ev_away     NUMERIC(6,4),
            ev_home     NUMERIC(6,4),
            movement_boost NUMERIC(5,4) DEFAULT 0,
            model_total NUMERIC(4,1),
            book_total  NUMERIC(4,1),
            total_edge  NUMERIC(5,4),
            ev_over     NUMERIC(6,4),
            ev_under    NUMERIC(6,4),
            recommended_play  VARCHAR(20),
            confidence_tier   VARCHAR(10),
            edge_pct          NUMERIC(5,4)
        )
    """)

    # ── Sprint-2 new columns ─────────────────────────────────────────────────
    op.execute("ALTER TABLE games ADD COLUMN IF NOT EXISTS weather_condition VARCHAR(50)")
    op.execute("ALTER TABLE games ADD COLUMN IF NOT EXISTS weather_temp      INTEGER")
    op.execute("ALTER TABLE games ADD COLUMN IF NOT EXISTS weather_wind      VARCHAR(100)")
    op.execute("ALTER TABLE games ADD COLUMN IF NOT EXISTS weather_wind_mph  INTEGER")
    op.execute("ALTER TABLE games ADD COLUMN IF NOT EXISTS weather_wind_dir  VARCHAR(30)")

    op.execute("ALTER TABLE predictions ADD COLUMN IF NOT EXISTS sim_totals_json TEXT")

    # Indexes
    op.execute("CREATE INDEX IF NOT EXISTS ix_games_game_date ON games (game_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_games_season    ON games (season)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_predictions_game_id ON predictions (game_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_edge_results_game_id ON edge_results (game_id)")


def downgrade() -> None:
    op.execute("ALTER TABLE games DROP COLUMN IF EXISTS weather_condition")
    op.execute("ALTER TABLE games DROP COLUMN IF EXISTS weather_temp")
    op.execute("ALTER TABLE games DROP COLUMN IF EXISTS weather_wind")
    op.execute("ALTER TABLE games DROP COLUMN IF EXISTS weather_wind_mph")
    op.execute("ALTER TABLE games DROP COLUMN IF EXISTS weather_wind_dir")
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS sim_totals_json")
