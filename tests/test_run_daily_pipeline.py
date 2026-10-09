import asyncio
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Barrier
from unittest.mock import AsyncMock, Mock, patch

import pytest
import requests
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import run_daily_pipeline as runner
from app.db import Base, get_db
from app.middleware.limiter import limiter
from app.models.schema import DailyPipelineRun, Game
from app.routes.daily import router
from app.services import pipeline_service
from app.services.daily_run_guard import DailyRunGuard

TARGET_DATE = date(2026, 10, 9)
RESULT = {"date": str(TARGET_DATE), "steps": {"sync_games": {"status": "ok", "total": 2}}}


def response(status=200, payload=None):
    resp = requests.Response()
    resp.status_code = status
    resp.json = Mock(return_value=RESULT if payload is None else payload)
    return resp


@pytest.fixture
def client_mocks():
    with patch.object(runner.requests, "post") as post, \
            patch.object(runner.requests, "get") as get, \
            patch.object(runner, "load_dotenv", return_value=False):
        yield post, get, Mock()


def test_success_first_try_prints_original_steps(client_mocks, capsys):
    post, get, sleep = client_mocks
    post.return_value = response()
    assert runner.run_pipeline(sleep=sleep) == RESULT
    post.assert_called_once()
    get.assert_not_called()
    sleep.assert_not_called()
    assert post.call_args.kwargs["timeout"] == (30, 300)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Starting daily MLB pipeline..."
    assert "Daily pipeline attempt 1/3" in lines
    assert lines[-2:] == ["Date: 2026-10-09", "  sync_games: {'status': 'ok', 'total': 2}"]


def test_read_timeout_then_success(client_mocks, capsys):
    post, get, sleep = client_mocks
    post.side_effect = [requests.exceptions.ReadTimeout(), response()]
    assert runner.run_pipeline(sleep=sleep) == RESULT
    assert post.call_count == 2
    get.assert_not_called()
    sleep.assert_called_once_with(30)
    assert "ReadTimeout; waiting 30 s" in capsys.readouterr().out


def test_500_500_then_success(client_mocks):
    post, get, sleep = client_mocks
    post.side_effect = [response(500), response(500), response()]
    assert runner.run_pipeline(sleep=sleep) == RESULT
    assert post.call_count == 3
    assert [call.args for call in sleep.call_args_list] == [(30,), (120,)]


def test_three_failures_raise(client_mocks, capsys):
    post, get, sleep = client_mocks
    post.side_effect = requests.exceptions.ReadTimeout()
    with pytest.raises(requests.exceptions.ReadTimeout):
        runner.run_pipeline(sleep=sleep)
    assert post.call_count == 3
    assert [call.args for call in sleep.call_args_list] == [(30,), (120,)]
    assert "Attempt 3/3 failed: ReadTimeout; no retry" in capsys.readouterr().out


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429, 501])
def test_nonretryable_http_fails_fast(client_mocks, status):
    post, get, sleep = client_mocks
    post.return_value = response(status)
    with pytest.raises(requests.exceptions.HTTPError):
        runner.run_pipeline(sleep=sleep)
    post.assert_called_once()
    sleep.assert_not_called()


@pytest.mark.parametrize("status", [502, 503, 504])
def test_gateway_errors_retry(client_mocks, status):
    post, get, sleep = client_mocks
    post.side_effect = [response(status), response()]
    assert runner.run_pipeline(sleep=sleep) == RESULT
    assert post.call_count == 2
    sleep.assert_called_once_with(30)


@pytest.mark.parametrize("error", [requests.exceptions.ConnectionError, requests.exceptions.ConnectTimeout])
def test_connection_errors_retry(client_mocks, error):
    post, get, sleep = client_mocks
    post.side_effect = [error(), response()]
    assert runner.run_pipeline(sleep=sleep) == RESULT
    assert post.call_count == 2


def test_pending_polls_without_starting_another_run(client_mocks, capsys):
    post, get, sleep = client_mocks
    pending = response(202, {"status": "running"})
    post.return_value = pending
    get.side_effect = [pending, response()]
    assert runner.run_pipeline(sleep=sleep) == RESULT
    post.assert_called_once()
    assert get.call_count == 2
    assert [call.args for call in sleep.call_args_list] == [(30,), (30,)]
    assert get.call_args.kwargs["params"] == post.call_args.kwargs["params"]
    assert "already running" in capsys.readouterr().out


def test_timeout_retry_finds_completed_original_request(client_mocks):
    post, get, sleep = client_mocks
    post.side_effect = [requests.exceptions.ReadTimeout(), response(202, {"status": "running"})]
    get.return_value = response()
    assert runner.run_pipeline(sleep=sleep) == RESULT
    assert post.call_count == 2
    get.assert_called_once()
    assert post.call_args_list[0].kwargs["params"] == post.call_args_list[1].kwargs["params"]


def test_pending_poll_failure_uses_retry_policy(client_mocks):
    post, get, sleep = client_mocks
    post.side_effect = [response(202, {"status": "running"}), response()]
    get.return_value = response(500)
    assert runner.run_pipeline(sleep=sleep) == RESULT
    assert post.call_count == 2


def test_pending_poll_auth_failure_fails_fast(client_mocks):
    post, get, sleep = client_mocks
    post.return_value = response(202, {"status": "running"})
    get.return_value = response(401)
    with pytest.raises(requests.exceptions.HTTPError):
        runner.run_pipeline(sleep=sleep)
    post.assert_called_once()
    get.assert_called_once()


def test_pending_never_claims_success_and_is_bounded(client_mocks):
    post, get, sleep = client_mocks
    post.return_value = response(202, {"status": "running"})
    get.return_value = response(202, {"status": "running"})
    with pytest.raises(requests.exceptions.ReadTimeout):
        runner.run_pipeline(sleep=sleep, max_status_checks=2)
    assert post.call_count == 3
    assert get.call_count == 6


def test_retry_settings_are_injectable(client_mocks):
    post, get, sleep = client_mocks
    post.side_effect = [requests.exceptions.ReadTimeout(), response()]
    runner.run_pipeline(sleep=sleep, max_attempts=2, backoff_seconds=(7,), read_timeout=42)
    sleep.assert_called_once_with(7)
    assert post.call_args.kwargs["timeout"] == (30, 42)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(bind=engine) as session:
        yield session
    engine.dispose()


def test_guard_one_owner_and_persistent_cached_result(db):
    guard = DailyRunGuard(db, TARGET_DATE)
    assert guard.claim() is None
    with Session(bind=db.get_bind()) as second_db:
        other = DailyRunGuard(second_db, TARGET_DATE)
        assert other.claim()["status"] == "running"
        guard.complete(RESULT)
        assert other.claim() == RESULT
        assert other.status() == RESULT
    assert db.query(DailyPipelineRun).count() == 1


def test_guard_retries_only_before_side_effects(db):
    guard = DailyRunGuard(db, TARGET_DATE)
    assert guard.claim() is None
    guard.fail()
    assert guard.status()["status"] == "failed"
    assert guard.claim() is None
    guard.start_side_effects()
    guard.fail()
    assert DailyRunGuard(db, TARGET_DATE).claim()["status"] == "blocked"


def test_guard_crashed_owner_and_other_dates(db):
    assert DailyRunGuard(db, TARGET_DATE).claim() is None
    # No automatic lease expiry: a killed worker might already have sent Discord.
    assert DailyRunGuard(db, TARGET_DATE).claim()["status"] == "running"
    assert DailyRunGuard(db, date(2026, 10, 10)).claim() is None


def test_new_migration_supports_the_guard():
    migration_path = Path(__file__).resolve().parents[1] / "alembic/versions/f9b0c1d2e3f4_add_daily_pipeline_guard.py"
    spec = importlib.util.spec_from_file_location("daily_guard_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    with Session(bind=engine) as session:
        guard = DailyRunGuard(session, TARGET_DATE)
        assert guard.claim() is None
        guard.complete(RESULT)
        assert guard.status() == RESULT
    engine.dispose()


@pytest.mark.parametrize("previously_failed", [False, True])
def test_guard_atomic_claim_across_connections(tmp_path, previously_failed):
    engine = create_engine(f"sqlite:///{(tmp_path / 'guard.db').as_posix()}")
    DailyPipelineRun.__table__.create(engine)
    if previously_failed:
        with Session(bind=engine) as session:
            guard = DailyRunGuard(session, TARGET_DATE)
            guard.claim()
            guard.fail()
    barrier = Barrier(2)

    def claim():
        with Session(bind=engine) as session:
            barrier.wait(timeout=5)
            return DailyRunGuard(session, TARGET_DATE).claim()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert results.count(None) == 1
    assert [value["status"] for value in results if value is not None] == ["running"]
    engine.dispose()


def test_overlapping_pipeline_does_not_execute_twice(db):
    async def exercise():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def body(session, target_date, guard):
            entered.set()
            await finish.wait()
            return RESULT

        with patch.object(pipeline_service, "_run_daily_pipeline", side_effect=body) as work:
            first = asyncio.create_task(pipeline_service.run_daily_pipeline(db, TARGET_DATE))
            await entered.wait()
            with Session(bind=db.get_bind()) as other_db:
                overlapping = await pipeline_service.run_daily_pipeline(other_db, TARGET_DATE)
            assert overlapping["status"] == "running"
            finish.set()
            assert await first == RESULT
            assert await pipeline_service.run_daily_pipeline(db, TARGET_DATE) == RESULT
            assert work.call_count == 1

    asyncio.run(exercise())


def test_pipeline_dns_failure_releases_for_retry(db):
    with patch.object(pipeline_service, "resolve_completed_games", side_effect=requests.exceptions.ConnectionError()):
        with pytest.raises(requests.exceptions.ConnectionError):
            asyncio.run(pipeline_service.run_daily_pipeline(db, TARGET_DATE))
    assert DailyRunGuard(db, TARGET_DATE).status()["status"] == "failed"
    with patch.object(pipeline_service, "_run_daily_pipeline", new=AsyncMock(return_value=RESULT)) as work:
        assert asyncio.run(pipeline_service.run_daily_pipeline(db, TARGET_DATE)) == RESULT
        work.assert_awaited_once()


@pytest.mark.parametrize("side_effects_started", [False, True])
def test_cancelled_request_preserves_safe_retry_boundary(db, side_effects_started):
    async def body(session, target_date, guard):
        if side_effects_started:
            guard.start_side_effects()
        raise asyncio.CancelledError()

    with patch.object(pipeline_service, "_run_daily_pipeline", side_effect=body):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(pipeline_service.run_daily_pipeline(db, TARGET_DATE))
    expected = "blocked" if side_effects_started else "failed"
    assert DailyRunGuard(db, TARGET_DATE).status()["status"] == expected


@pytest.mark.parametrize("failure_step", ["odds", "alerts"])
def test_failure_after_series_alert_does_not_resend_or_refetch(db, failure_step):
    game = Game(game_id=1, game_date=TARGET_DATE, season=2026, away_team="Away", home_team="Home")
    db.add(game)
    db.commit()
    with patch.object(pipeline_service, "resolve_completed_games", return_value={}), \
            patch.object(pipeline_service, "sync_games_for_date", return_value={}), \
            patch.object(pipeline_service, "deactivate_stale_active_predictions", return_value=0), \
            patch.object(pipeline_service, "run_predictions_for_date", return_value={}), \
            patch("app.services.sandbox_simulator.run_v4_sandbox", return_value={"is_series_opener": True}), \
            patch("app.services.sandbox_alerts.send_series_opener_alert") as send, \
            patch.object(pipeline_service, "sync_odds_for_snapshot", new=AsyncMock(
                side_effect=RuntimeError("late failure") if failure_step == "odds" else None,
                return_value=([], {"status": "ok"}),
            )) as odds, \
            patch.object(pipeline_service, "calculate_edges_for_today", return_value={}), \
            patch.object(pipeline_service, "send_daily_alerts", side_effect=RuntimeError("late failure")) as alerts:
        with pytest.raises(RuntimeError, match="late failure"):
            asyncio.run(pipeline_service.run_daily_pipeline(db, TARGET_DATE))
        assert asyncio.run(pipeline_service.run_daily_pipeline(db, TARGET_DATE))["status"] == "blocked"
        send.assert_called_once()
        odds.assert_awaited_once()
        assert alerts.call_count == (1 if failure_step == "alerts" else 0)


def test_guard_cached_pipeline_does_not_repeat_quota_or_alerts(db):
    with patch.object(pipeline_service, "resolve_completed_games", return_value={}), \
            patch.object(pipeline_service, "sync_games_for_date", return_value={}), \
            patch.object(pipeline_service, "deactivate_stale_active_predictions", return_value=0), \
            patch.object(pipeline_service, "run_predictions_for_date", return_value={}), \
            patch.object(pipeline_service, "sync_odds_for_snapshot", new=AsyncMock(return_value=([], {"status": "ok"}))) as odds, \
            patch.object(pipeline_service, "calculate_edges_for_today", return_value={}) as edges, \
            patch.object(pipeline_service, "send_daily_alerts", return_value={}) as alerts:
        result = asyncio.run(pipeline_service.run_daily_pipeline(db, TARGET_DATE))
        assert asyncio.run(pipeline_service.run_daily_pipeline(db, TARGET_DATE)) == result
        odds.assert_awaited_once()
        edges.assert_called_once()
        alerts.assert_called_once()


@pytest.fixture
def api(db, monkeypatch):
    application = FastAPI()
    application.state.limiter = limiter
    application.include_router(router)
    application.dependency_overrides[get_db] = lambda: db
    monkeypatch.setenv("API_SECRET_KEY", "offline-test-key")
    monkeypatch.setattr(limiter, "enabled", False)
    with TestClient(application) as client:
        yield client


def test_routes_running_completed_blocked_and_missing(api, db):
    kwargs = {"headers": {"X-API-Key": "offline-test-key"}, "params": {"target_date": str(TARGET_DATE)}}
    assert api.get("/api/daily-run/status", **kwargs).status_code == 404
    guard = DailyRunGuard(db, TARGET_DATE)
    guard.claim()
    pending = api.post("/api/daily-run", **kwargs)
    assert pending.status_code == 202
    assert pending.json()["status"] == "running"
    assert pending.headers["Retry-After"] == "30"
    assert api.get("/api/daily-run/status", **kwargs).status_code == 202
    guard.start_side_effects()
    guard.fail()
    assert api.post("/api/daily-run", **kwargs).status_code == 500
    assert api.get("/api/daily-run/status", **kwargs).status_code == 500
    guard.complete(RESULT)
    assert api.get("/api/daily-run/status", **kwargs).json() == RESULT
    assert api.post("/api/daily-run", **kwargs).json() == RESULT


def test_status_route_requires_api_key(api):
    assert api.get("/api/daily-run/status").status_code == 403
