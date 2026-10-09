"""Frozen prospective gate configuration and enrollment; no existing data writes."""
import sqlalchemy as sa
from alembic import op

revision = "fc01b2c3d4e5"
down_revision = "fb01b2c3d4e5"
branch_labels = depends_on = None


def upgrade():
    op.create_table("gate_deployments", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market", sa.String(20), nullable=False, unique=True),
        sa.Column("candidate_version", sa.String(100), nullable=False),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cohort_size", sa.Integer(), nullable=False),
        sa.Column("horizon_minutes", sa.Integer(), nullable=False),
        sa.Column("tolerance_minutes", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(100), nullable=False))
    op.create_table("gate_members", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("deployment_id", sa.Integer(), sa.ForeignKey("gate_deployments.id"), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("forecast_id", sa.Integer(), sa.ForeignKey("forecast_history.id"), nullable=False, unique=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("deployment_id", "game_id", name="uq_gate_game"))
    for table in ("gate_deployments", "gate_members"):
        if op.get_bind().dialect.name == "postgresql":
            op.execute(f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
                       "FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()")
        elif op.get_bind().dialect.name == "sqlite":
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} "
                           "BEGIN SELECT RAISE(ABORT, 'History is append-only'); END")
        else:
            raise RuntimeError("Unsupported history database")


def downgrade():
    raise RuntimeError("Prospective gate history cannot be downgraded")
