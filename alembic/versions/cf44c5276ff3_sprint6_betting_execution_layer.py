"""sprint6_betting_execution_layer

Adds isolated betting execution tables:
  - bet_orders
  - bet_executions
  - bankroll_snapshots

These tables are ONLY written to by the execution service.
No existing pipeline tables are modified.

Revision ID: cf44c5276ff3
Revises: f1a2b3c4d5e6
Create Date: 2026-03-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "cf44c5276ff3"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bankroll_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider_mode", sa.String(length=10), nullable=False),
        sa.Column("sportsbook", sa.String(length=50), nullable=False),
        sa.Column("bankroll", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("available_balance", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "bet_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("sportsbook", sa.String(length=50), nullable=False),
        sa.Column("provider_mode", sa.String(length=10), nullable=False),
        sa.Column("market_type", sa.String(length=20), nullable=False),
        sa.Column("side", sa.String(length=20), nullable=False),
        sa.Column("requested_line", sa.Numeric(precision=5, scale=1), nullable=True),
        sa.Column("requested_odds", sa.Integer(), nullable=True),
        sa.Column("requested_stake", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("edge_pct", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("ev", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("confidence", sa.String(length=10), nullable=True),
        sa.Column("source_rank", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.game_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bet_orders_game_id", "bet_orders", ["game_id"], unique=False)

    op.create_table(
        "bet_executions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bet_order_id", sa.Integer(), nullable=False),
        sa.Column("external_bet_id", sa.String(length=100), nullable=True),
        sa.Column("placed_line", sa.Numeric(precision=5, scale=1), nullable=True),
        sa.Column("placed_odds", sa.Integer(), nullable=True),
        sa.Column("placed_stake", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fill_status", sa.String(length=20), nullable=True),
        sa.Column("raw_response_json", sa.Text(), nullable=True),
        sa.Column("settled_result", sa.String(length=10), nullable=True),
        sa.Column("profit_loss_units", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("profit_loss_dollars", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["bet_order_id"], ["bet_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bet_executions_bet_order_id", "bet_executions", ["bet_order_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_bet_executions_bet_order_id", table_name="bet_executions")
    op.drop_table("bet_executions")
    op.drop_index("ix_bet_orders_game_id", table_name="bet_orders")
    op.drop_table("bet_orders")
    op.drop_table("bankroll_snapshots")
