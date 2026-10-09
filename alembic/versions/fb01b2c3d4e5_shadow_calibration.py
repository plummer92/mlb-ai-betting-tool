"""Shadow fits and immutable forecast grades."""
import sqlalchemy as sa
from alembic import op

revision = "fb01b2c3d4e5"
down_revision = "fa01b2c3d4e5"
branch_labels = depends_on = None


def upgrade():
    op.create_table("shadow_fits", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("market", sa.String(20), nullable=False), sa.Column("period", sa.String(7), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("train_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("model_version", "market", "period", name="uq_shadow_fit_period"))
    op.create_table("forecast_grades", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("forecast_id", sa.Integer(), sa.ForeignKey("forecast_history.id"), nullable=False, unique=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("home_score", sa.Integer(), nullable=False), sa.Column("away_score", sa.Integer(), nullable=False),
        sa.Column("y", sa.Integer()), sa.Column("push", sa.Boolean(), nullable=False),
        sa.Column("brier", sa.Float()), sa.Column("log_loss", sa.Float()))
    for table in ("shadow_fits", "forecast_grades"):
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
    raise RuntimeError("Immutable history cannot be downgraded")
