"""harden_prediction_and_edge_lineage

Revision ID: 9c4c4f8d2b10
Revises: 6ab120e71daf
Create Date: 2026-04-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9c4c4f8d2b10"
down_revision: Union[str, Sequence[str], None] = "6ab120e71daf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("predictions", sa.Column("run_stage", sa.String(length=32), nullable=False, server_default="legacy"))
    op.add_column("predictions", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("predictions", sa.Column("calibration_result_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_predictions_calibration_result",
        "predictions",
        "backtest_results",
        ["calibration_result_id"],
        ["id"],
    )

    op.add_column("edge_results", sa.Column("run_stage", sa.String(length=32), nullable=False, server_default="legacy"))
    op.add_column("edge_results", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))

    op.execute(
        """
        WITH ranked AS (
            SELECT
                prediction_id,
                ROW_NUMBER() OVER (
                    PARTITION BY game_id, run_stage
                    ORDER BY created_at DESC, prediction_id DESC
                ) AS rn
            FROM predictions
        )
        UPDATE predictions AS p
        SET is_active = CASE WHEN ranked.rn = 1 THEN TRUE ELSE FALSE END
        FROM ranked
        WHERE p.prediction_id = ranked.prediction_id
        """
    )

    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY game_id, run_stage
                    ORDER BY calculated_at DESC, id DESC
                ) AS rn
            FROM edge_results
        )
        UPDATE edge_results AS e
        SET is_active = CASE WHEN ranked.rn = 1 THEN TRUE ELSE FALSE END
        FROM ranked
        WHERE e.id = ranked.id
        """
    )

    op.alter_column("predictions", "run_stage", server_default=None)
    op.alter_column("predictions", "is_active", server_default=None)
    op.alter_column("edge_results", "run_stage", server_default=None)
    op.alter_column("edge_results", "is_active", server_default=None)


def downgrade() -> None:
    op.drop_column("edge_results", "is_active")
    op.drop_column("edge_results", "run_stage")

    op.drop_constraint("fk_predictions_calibration_result", "predictions", type_="foreignkey")
    op.drop_column("predictions", "calibration_result_id")
    op.drop_column("predictions", "is_active")
    op.drop_column("predictions", "run_stage")
