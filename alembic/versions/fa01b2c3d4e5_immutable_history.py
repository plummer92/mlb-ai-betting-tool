"""Append-only observation history; no legacy backfill or rewrites."""
import sqlalchemy as sa
from alembic import op

revision = "fa01b2c3d4e5"
down_revision = "f9b0c1d2e3f4"
branch_labels = depends_on = None


def upgrade():
    op.create_table("price_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("sportsbook", sa.String(50), nullable=False),
        sa.Column("market", sa.String(20), nullable=False),
        sa.Column("line", sa.Float()),
        sa.Column("first_price", sa.Integer(), nullable=False),
        sa.Column("second_price", sa.Integer(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("scheduled_start", sa.DateTime(timezone=True)))
    op.create_table("forecast_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_start", sa.DateTime(timezone=True)),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("calibration_version", sa.String(100), nullable=False),
        sa.Column("feature_hash", sa.String(64), nullable=False),
        sa.Column("inputs_json", sa.Text(), nullable=False),
        sa.Column("outputs_json", sa.Text(), nullable=False),
        sa.Column("market", sa.String(20)),
        sa.Column("sportsbook", sa.String(50)),
        sa.Column("line", sa.Float()),
        sa.Column("price_id", sa.Integer(), sa.ForeignKey("price_history.id")))
    op.create_table("decision_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_start", sa.DateTime(timezone=True)),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("decision_status", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("play", sa.String(20)),
        sa.Column("market", sa.String(20)),
        sa.Column("forecast_id", sa.Integer(), sa.ForeignKey("forecast_history.id")),
        sa.Column("price_id", sa.Integer(), sa.ForeignKey("price_history.id")),
        sa.Column("payload_json", sa.Text(), nullable=False))
    for table, time in (("price_history", "fetched_at"), ("forecast_history", "created_at"),
                        ("decision_history", "created_at")):
        op.create_index(f"ix_{table}_game_id", table, ["game_id"])
        op.create_index(f"ix_{table}_{time}", table, [time])
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("""CREATE FUNCTION reject_history_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'History is append-only'; END; $$ LANGUAGE plpgsql""")
    for table in ("price_history", "forecast_history", "decision_history"):
        if dialect == "postgresql":
            op.execute(f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
                       "FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()")
        elif dialect == "sqlite":
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} "
                           "BEGIN SELECT RAISE(ABORT, 'History is append-only'); END")
        else:
            raise RuntimeError("History triggers require PostgreSQL or SQLite")


def downgrade():
    raise RuntimeError("Immutable history is deliberately irreversible; restore a backup to roll back")
