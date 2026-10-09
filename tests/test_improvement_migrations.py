import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
import pytest


def test_additive_migration_chain_preserves_existing_rows_and_guards_all_history():
    paths = ["fa01b2c3d4e5_immutable_history.py", "fb01b2c3d4e5_shadow_calibration.py", "fc01b2c3d4e5_prospective_gate.py"]
    engine = create_engine("sqlite:///:memory:")
    previous = "f9b0c1d2e3f4"
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE existing_sentinel (id INTEGER PRIMARY KEY, value TEXT)"))
        conn.execute(text("INSERT INTO existing_sentinel VALUES (1, 'preserved')"))
        for filename in paths:
            spec = importlib.util.spec_from_file_location(filename[:12], Path("alembic/versions")/filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            assert module.down_revision == previous
            previous = module.revision
            module.op = Operations(MigrationContext.configure(conn))
            module.upgrade()
        assert conn.execute(text("SELECT value FROM existing_sentinel")).scalar() == "preserved"
        tables = {"price_history", "forecast_history", "decision_history", "shadow_fits", "forecast_grades", "gate_deployments", "gate_members"}
        assert set(inspect(conn).get_table_names()) == tables | {"existing_sentinel"}
        triggers = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))}
        assert len(triggers) == 2*len(tables)
        conn.execute(text("INSERT INTO gate_deployments (market,candidate_version,deployed_at,cohort_size,horizon_minutes,tolerance_minutes,policy_version) "
                          "VALUES ('moneyline','v1','2026-10-09',300,45,5,'p1')"))
        with pytest.raises(Exception, match="append-only"):
            conn.execute(text("UPDATE gate_deployments SET cohort_size=1"))
    engine.dispose()
