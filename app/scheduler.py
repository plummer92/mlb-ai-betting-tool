import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionLocal
from app.models.schema import Game, GameOdds
from app.services.alert_service import create_and_send_alert_for_game, create_and_send_alerts_for_today
from app.services.backtest_service import run_logistic_regression
from app.services.edge_service import calculate_edge_for_game
from app.services.odds_service import (
    SnapshotType,
    compute_line_movement,
    fetch_and_store_odds,
    get_latest_odds_snapshot,
    is_odds_snapshot_fresh,
)
from app.services.pipeline_service import (
    calculate_edges_for_today,
    run_predictions_for_date,
    sync_games_for_date,
)
from app.services.prediction_service import deactivate_stale_active_predictions
from app.services.ranked_alerts import send_ranked_bets_to_discord_job
from app.services.review_service import resolve_completed_games

scheduler = AsyncIOScheduler(timezone="America/New_York")
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
PREGAME_REUSE_WINDOW_MINUTES = 15
logger = logging.getLogger(__name__)


@scheduler.scheduled_job(CronTrigger(hour=9, minute=0, timezone="America/New_York"))
def resolve_yesterday_job():
    db = SessionLocal()
    try:
        result = resolve_completed_games(db)
        print(f"[scheduler] 9am resolve: {result}")
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.exception("[scheduler] Resolve error")
    finally:
        db.close()


@scheduler.scheduled_job(CronTrigger(hour=9, minute=30, timezone="America/New_York"))
def sync_today_games_job():
    db = SessionLocal()
    try:
        today = datetime.now(ET).date()
        result = sync_games_for_date(db, today)
        print(f"[scheduler] Game sync: {result['total']} total, {result['new']} new")
    except (SQLAlchemyError, RuntimeError, ValueError):
        db.rollback()
        logger.exception("[scheduler] Game sync error")
    finally:
        db.close()


@scheduler.scheduled_job(CronTrigger(hour=10, minute=0, timezone="America/New_York"))
async def morning_odds_snapshot():
    db = SessionLocal()
    try:
        stored = await fetch_and_store_odds(db, snapshot_type=SnapshotType.open)
        print(f"[scheduler] Morning snapshot: {len(stored)} odds rows stored")
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.exception("[scheduler] Morning odds error")
    finally:
        db.close()


@scheduler.scheduled_job(CronTrigger(hour=10, minute=15, timezone="America/New_York"))
def run_monte_carlo_and_schedule_pregame():
    db = SessionLocal()
    try:
        today = datetime.now(ET).date()
        deactivated = deactivate_stale_active_predictions(db, keep_on_or_after=today)
        if deactivated:
            print(f"[scheduler] Prediction cleanup: deactivated={deactivated}")

        result = run_predictions_for_date(
            db,
            today,
            run_stage="daily_open",
            diagnostic_label="scheduler-daily-open",
            include_sandbox=True,
        )
        print(f"[scheduler] Monte Carlo: {result['ran']} ok, {len(result['errors'])} errors")

        games = db.query(Game).filter(Game.game_date == today).all()
        for game in games:
            if not game.start_time:
                continue
            try:
                game_dt = datetime.fromisoformat(game.start_time)
                if game_dt.tzinfo is None:
                    game_dt = game_dt.replace(tzinfo=UTC)
            except ValueError:
                logger.warning(
                    "[scheduler] Could not parse start_time for game %s: %s",
                    game.game_id,
                    game.start_time,
                )
                continue

            pregame_trigger_time = game_dt - timedelta(minutes=45)
            if pregame_trigger_time <= datetime.now(UTC):
                continue

            job_id = f"pregame_{game.game_id}"
            if scheduler.get_job(job_id):
                continue

            scheduler.add_job(
                run_pregame_snapshot,
                trigger=DateTrigger(run_date=pregame_trigger_time),
                args=[game.game_id],
                id=job_id,
                replace_existing=True,
            )
            logger.info(
                "[scheduler] Pregame job scheduled for game %s at %s",
                game.game_id,
                pregame_trigger_time,
            )
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.exception("[scheduler] Monte Carlo job error")
    finally:
        db.close()


@scheduler.scheduled_job(CronTrigger(hour=10, minute=30, timezone="America/New_York"))
async def calculate_edges_job():
    db = SessionLocal()
    try:
        games = db.query(Game).filter(Game.game_date == datetime.now(ET).date()).all()
        stored = []
        for game in games:
            latest_open = get_latest_odds_snapshot(
                db,
                game_id=game.game_id,
                snapshot_type=SnapshotType.open,
            )
            if latest_open is not None and is_odds_snapshot_fresh(latest_open):
                stored.append(latest_open)
        if len(stored) != len(games):
            stored = await fetch_and_store_odds(db, snapshot_type=SnapshotType.open)

        result = calculate_edges_for_today(
            db,
            run_stage="daily_open",
            snapshot_type=SnapshotType.open,
            odds_rows=stored,
            diagnostic_label="scheduler-daily-open",
        )
        print(f"[scheduler] Edges calculated: {result}")
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.exception("[scheduler] Edge calculation error")
    finally:
        db.close()


@scheduler.scheduled_job(CronTrigger(hour=10, minute=45, timezone="America/New_York"))
def send_morning_alerts_job():
    db = SessionLocal()
    try:
        result = create_and_send_alerts_for_today(db)
        print(f"[scheduler] Morning alerts: {result}")
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.exception("[scheduler] Alert error")
    finally:
        db.close()


async def run_pregame_snapshot(game_id: int):
    db = SessionLocal()
    try:
        latest_existing = get_latest_odds_snapshot(
            db,
            game_id=game_id,
            snapshot_type=SnapshotType.pregame,
        )
        if latest_existing is not None and is_odds_snapshot_fresh(
            latest_existing,
            max_age_minutes=PREGAME_REUSE_WINDOW_MINUTES,
        ):
            stored = (
                db.query(GameOdds)
                .filter(
                    GameOdds.snapshot_type == SnapshotType.pregame,
                    GameOdds.fetched_at == latest_existing.fetched_at,
                )
                .all()
            )
            print(
                f"[scheduler] Pregame snapshot reused for game {game_id}: "
                f"rows={len(stored)} fetched_at={latest_existing.fetched_at}"
            )
        else:
            stored = await fetch_and_store_odds(db, snapshot_type=SnapshotType.pregame)
            print(f"[scheduler] Pregame snapshot fetched: {len(stored)} rows")

        movement = compute_line_movement(db, game_id)
        if movement:
            print(
                f"[scheduler] Game {game_id} - "
                f"away move: {movement.away_prob_move:+.3f}, "
                f"home move: {movement.home_prob_move:+.3f}, "
                f"sharp_away={movement.sharp_away}, "
                f"sharp_home={movement.sharp_home}"
            )

        odds_by_game = {row.game_id: row for row in stored}
        edge_result = calculate_edge_for_game(
            db,
            game_id,
            run_stage="pregame",
            snapshot_type=SnapshotType.pregame,
            odds_snapshot=odds_by_game.get(game_id),
            fallback_policy="reuse_fresh_same_stage",
            movement=movement,
        )
        print(f"[scheduler] Edge recalculated for game {game_id}: {edge_result}")

        alert_result = create_and_send_alert_for_game(db, game_id)
        print(f"[scheduler] Pregame alert for game {game_id}: {alert_result}")
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.exception("[scheduler] Pregame snapshot error for game %s", game_id)
    finally:
        db.close()


@scheduler.scheduled_job(CronTrigger(hour="15-23", minute="*/15", timezone="America/New_York"))
def resolve_completed_games_job():
    db = SessionLocal()
    try:
        result = resolve_completed_games(db)
        print(f"[scheduler] Postgame resolver: {result}")
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.exception("[scheduler] Postgame resolve error")
    finally:
        db.close()


@scheduler.scheduled_job(CronTrigger(day_of_week="mon", hour=6, minute=0, timezone="America/New_York"))
def weekly_backtest_job():
    db = SessionLocal()
    try:
        result = run_logistic_regression(db, [2022, 2023, 2024])
        print(
            f"[scheduler] Weekly backtest: seasons={result.seasons}, "
            f"n_games={result.n_games}, accuracy={result.accuracy:.4f}, "
            f"cv={result.cv_accuracy:.4f}, brier={result.brier_score:.4f}, "
            f"calibrated={result.calibration_params_json is not None}"
        )
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.exception("[scheduler] Weekly backtest failed")
    finally:
        db.close()


@scheduler.scheduled_job(CronTrigger(hour=11, minute=0, timezone="America/New_York"))
def ranked_bets_discord_job():
    try:
        result = send_ranked_bets_to_discord_job(limit=10, active_only=True)
        print(f"[scheduler] Discord summary: {result}")
    except (RuntimeError, ValueError):
        logger.exception("[scheduler] Discord summary error")
