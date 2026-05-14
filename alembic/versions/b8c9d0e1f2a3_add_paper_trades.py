"""add paper trades

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-05-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paper_trades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bet_alert_id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("prediction_id", sa.Integer(), nullable=True),
        sa.Column("edge_result_id", sa.Integer(), nullable=True),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("play", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.String(length=10), nullable=True),
        sa.Column("edge_pct", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("ev", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("paper_stake", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("odds", sa.Integer(), nullable=True),
        sa.Column("line", sa.Numeric(precision=5, scale=1), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result", sa.String(length=10), nullable=True),
        sa.Column("profit_loss", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column(
            "placed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["bet_alert_id"], ["bet_alerts.id"]),
        sa.ForeignKeyConstraint(["edge_result_id"], ["edge_results.id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.game_id"]),
        sa.ForeignKeyConstraint(["prediction_id"], ["predictions.prediction_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bet_alert_id", name="uq_paper_trades_bet_alert"),
    )
    op.create_index("ix_paper_trades_bet_alert_id", "paper_trades", ["bet_alert_id"], unique=False)
    op.create_index("ix_paper_trades_edge_result_id", "paper_trades", ["edge_result_id"], unique=False)
    op.create_index("ix_paper_trades_game_date", "paper_trades", ["game_date"], unique=False)
    op.create_index("ix_paper_trades_game_id", "paper_trades", ["game_id"], unique=False)
    op.create_index("ix_paper_trades_prediction_id", "paper_trades", ["prediction_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_paper_trades_prediction_id", table_name="paper_trades")
    op.drop_index("ix_paper_trades_game_id", table_name="paper_trades")
    op.drop_index("ix_paper_trades_game_date", table_name="paper_trades")
    op.drop_index("ix_paper_trades_edge_result_id", table_name="paper_trades")
    op.drop_index("ix_paper_trades_bet_alert_id", table_name="paper_trades")
    op.drop_table("paper_trades")
