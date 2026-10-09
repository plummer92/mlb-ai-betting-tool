import importlib.util
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db import Base
from app.models.schema import ForecastHistory, PriceHistory, DecisionHistory, Game, GameOdds, Prediction, SnapshotType
from app.services.history_service import append_forecast, append_prices, append_decision, forecast_as_of, price_as_of
from app.services.prediction_service import store_prediction
from app.services.edge_service import calculate_edge_for_game

NOW = datetime(2026, 10, 9, 17, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Game(game_id=1, game_date=date(2026, 10, 9), season=2026,
            home_team="Home", away_team="Away", start_time=(NOW + timedelta(hours=2)).isoformat()))
        session.commit()
        yield session
    engine.dispose()


def test_prediction_upsert_keeps_both_frozen_forecasts(db):
    args = dict(game_id=1, model_version="live-v1", run_stage="pregame", sim_count=1000,
        away_win_pct=.4, home_win_pct=.6, calibrated_home_win_pct=None, calibrated_away_win_pct=None,
        projected_away_score=4., projected_home_score=5., projected_total=9., confidence_score=1.,
        recommended_side="home_ml", home_starter_xera=3., away_starter_xera=4., using_xera=True,
        kbb_adv=None, park_factor_adv=None, pythagorean_win_pct_adv=None, calibration_result_id=None,
        feature_inputs={"starter": 12, "era": 3.})
    first = store_prediction(db, **args)
    args.update(home_win_pct=.7, feature_inputs={"starter": 12, "era": 2.})
    second = store_prediction(db, **args)
    rows = db.query(ForecastHistory).order_by(ForecastHistory.id).all()
    assert first.prediction_id == second.prediction_id
    assert db.query(Prediction).count() == 1 and len(rows) == 2
    assert json.loads(rows[0].outputs_json)["home_win_pct"] == .6
    assert rows[0].feature_hash != rows[1].feature_hash
    assert rows[0].scheduled_start is not None
    rows[0].model_version = "changed"
    with pytest.raises(ValueError, match="append-only"):
        db.flush()
    db.rollback()


def test_quote_receipt_time_asof_and_same_book_line(db):
    odds = GameOdds(game_id=1, sportsbook="a", snapshot_type=SnapshotType.open,
                    home_ml=-110, away_ml=100, total_line=8, over_odds=-105, under_odds=-115)
    append_prices(db, odds, fetched_at=NOW, bookmaker={"last_update": "2026-10-09T15:00:00Z"})
    odds.home_ml = -150
    append_prices(db, odds, fetched_at=NOW + timedelta(minutes=1))
    db.commit()
    assert db.query(PriceHistory).count() == 4
    old = price_as_of(db, 1, NOW, sportsbook="a", market="moneyline")
    assert old.first_price == -110 and old.source_updated_at.hour == 15
    assert price_as_of(db, 1, NOW - timedelta(seconds=1), sportsbook="a", market="moneyline") is None
    assert price_as_of(db, 1, NOW, sportsbook="b", market="moneyline") is None
    assert price_as_of(db, 1, NOW, sportsbook="a", market="totals", line=8.5) is None
    assert price_as_of(db, 1, NOW, sportsbook="a", market="moneyline", strict=True) is None


def test_partial_provider_quote_is_preserved_and_mismatched_total_sides_are_not(db):
    from app.services.odds_service import _parse_bookmaker
    event = {"away_team":"Away", "home_team":"Home"}
    bookmaker = {"key":"a", "markets":[{"key":"totals", "outcomes":[
        {"name":"Over", "price":-110, "point":8}, {"name":"Under", "price":-110, "point":8}]}]}
    assert _parse_bookmaker(event, bookmaker, 1, SnapshotType.open)[0] is None
    odds, _ = _parse_bookmaker(event, bookmaker, 1, SnapshotType.open, allow_incomplete=True)
    append_prices(db, odds, bookmaker=bookmaker, fetched_at=NOW)
    db.commit()
    assert db.query(PriceHistory).count() == 1
    bookmaker["markets"][0]["outcomes"][1]["point"] = 8.5
    assert append_prices(db, odds, bookmaker=bookmaker, fetched_at=NOW) == []


def test_forecast_asof_and_rejected_decisions_append(db):
    append_forecast(db, game_id=1, model_version="v1", calibration_version="c1", inputs={"x": 1},
                    outputs={"p": .6}, created_at=NOW)
    db.commit()
    assert forecast_as_of(db, 1, NOW - timedelta(seconds=1)) is None
    assert forecast_as_of(db, 1, NOW).model_version == "v1"
    for reason in ("missing_lineage", "market_rejected"):
        append_decision(db, {"game_id": 1, "decision_status": "BLOCKED", "reason": reason}, created_at=NOW)
    db.commit()
    calculate_edge_for_game(db, 1, run_stage="bad", snapshot_type=SnapshotType.open)
    assert db.query(DecisionHistory).count() == 3
    assert db.query(DecisionHistory).order_by(DecisionHistory.id.desc()).first().reason == "disallowed_run_stage"


def test_migration_additive_chain_and_database_bulk_write_protection():
    path = Path("alembic/versions/fa01b2c3d4e5_immutable_history.py")
    spec = importlib.util.spec_from_file_location("history_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "f9b0c1d2e3f4"
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        module.op = Operations(MigrationContext.configure(conn))
        module.upgrade()
        conn.execute(text("INSERT INTO price_history (game_id,sportsbook,market,first_price,second_price,fetched_at) "
                          "VALUES (1,'a','moneyline',100,-110,'2026-10-09')"))
        with pytest.raises(Exception, match="append-only"):
            conn.execute(text("UPDATE price_history SET first_price=200"))
        with pytest.raises(Exception, match="append-only"):
            conn.execute(text("DELETE FROM price_history"))
    engine.dispose()
