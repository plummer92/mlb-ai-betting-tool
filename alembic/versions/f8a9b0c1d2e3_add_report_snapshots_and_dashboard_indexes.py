"""add report snapshots and dashboard indexes

Revision ID: f8a9b0c1d2e3
Revises: e1b2c3d4e5f7
Create Date: 2026-06-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, Sequence[str], None] = "e1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "report_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_name", sa.String(length=80), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ok"),
        sa.Column("runtime_ms", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_name", "report_date", name="uq_report_snapshot_name_date"),
    )
    op.create_index("ix_report_snapshots_name_generated", "report_snapshots", ["report_name", "generated_at"], unique=False)

    op.create_index("ix_games_date_start_id", "games", ["game_date", "start_time", "game_id"], unique=False)
    op.create_index("ix_predictions_game_stage_active", "predictions", ["game_id", "run_stage", "is_active"], unique=False)
    op.create_index("ix_game_odds_game_type_fetched_id", "game_odds", ["game_id", "snapshot_type", "fetched_at", "id"], unique=False)
    op.create_index("ix_edge_results_game_active_stage_calc", "edge_results", ["game_id", "is_active", "run_stage", "calculated_at", "id"], unique=False)
    op.create_index("ix_outcome_review_result_date_id", "game_outcomes_review", ["bet_result", "game_date", "id"], unique=False)
    op.create_index("ix_paper_trades_date_id", "paper_trades", ["game_date", "id"], unique=False)
    op.create_index("ix_reliever_workload_team_date", "reliever_workload", ["team_id", "date"], unique=False)
    op.create_index("ix_sandbox_v4_game_created_id", "sandbox_predictions_v4", ["game_id", "created_at", "id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sandbox_v4_game_created_id", table_name="sandbox_predictions_v4")
    op.drop_index("ix_reliever_workload_team_date", table_name="reliever_workload")
    op.drop_index("ix_paper_trades_date_id", table_name="paper_trades")
    op.drop_index("ix_outcome_review_result_date_id", table_name="game_outcomes_review")
    op.drop_index("ix_edge_results_game_active_stage_calc", table_name="edge_results")
    op.drop_index("ix_game_odds_game_type_fetched_id", table_name="game_odds")
    op.drop_index("ix_predictions_game_stage_active", table_name="predictions")
    op.drop_index("ix_games_date_start_id", table_name="games")
    op.drop_index("ix_report_snapshots_name_generated", table_name="report_snapshots")
    op.drop_table("report_snapshots")
