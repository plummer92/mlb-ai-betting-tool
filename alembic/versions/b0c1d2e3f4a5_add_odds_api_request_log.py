"""add odds api request log

Revision ID: b0c1d2e3f4a5
Revises: a9d4e6f2c8b1
Create Date: 2026-05-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "a9d4e6f2c8b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "odds_api_request_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("endpoint", sa.String(length=100), nullable=False),
        sa.Column("snapshot_type", sa.String(length=20), nullable=True),
        sa.Column("bookmakers", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("events_returned", sa.Integer(), nullable=True),
        sa.Column("raw_bytes", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_odds_api_request_log_requested_at", "odds_api_request_log", ["requested_at"], unique=False)
    op.create_index("ix_odds_api_request_log_snapshot_type", "odds_api_request_log", ["snapshot_type"], unique=False)
    op.create_index("ix_odds_api_request_log_status", "odds_api_request_log", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_odds_api_request_log_status", table_name="odds_api_request_log")
    op.drop_index("ix_odds_api_request_log_snapshot_type", table_name="odds_api_request_log")
    op.drop_index("ix_odds_api_request_log_requested_at", table_name="odds_api_request_log")
    op.drop_table("odds_api_request_log")
