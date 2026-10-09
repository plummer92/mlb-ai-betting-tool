"""Persist daily pipeline ownership and completed results.

Revision ID: f9b0c1d2e3f4
Revises: f8a9b0c1d2e3
"""
import sqlalchemy as sa
from alembic import op

revision = "f9b0c1d2e3f4"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_pipeline_runs",
        sa.Column("run_date", sa.Date(), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("side_effects_started", sa.Boolean(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("daily_pipeline_runs")
