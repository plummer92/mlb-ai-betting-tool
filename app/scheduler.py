import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionLocal
from app.models.schema import EdgeResult, Game, GameOdds, LineMovement, Prediction
from app.services.alert_service import create_and_send_alert_for_game, create_and_send_alerts_for_today
from app.services.backtest_service import apply_backtest_weights, get_latest_calibration_result, run_logistic_regression
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
from app.services.bullpen_calc import collect_reliever_workload
from app.services.manager_service import track_manager_decision
from app.services.review_service import resolve_completed_games

scheduler = AsyncIOScheduler(timezone="America/New_York")
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
PREGAME_REUSE_WINDOW_MINUTES = 15
PREGAME_BOARD_REUSE_WINDOW_MINUTES = 60
logger = logging.getLogger(__name__)


def _parse_game_start_time(game: Game) -> datetime | None:
    if not game.start_time:
        return None
    try:
        game_dt = datetime.fromisoformat(game.start_time)
    except ValueError:
        logger.warning(
            "[scheduler] Could not parse start_time for game %s: %s",
            game.game_id,
            game.start_time,
        )
        return None
    if game_dt.tzinfo is None:
        return game_dt.replace(tzinfo=UTC)
    return game_dt.astimezone(UTC)


def schedule_pregame_jobs_for_today(
    db,
    *,
    now_utc: datetime | None = None,
    catch_up_missing: bool = False,
) -> dict:
    """Schedule today's in-memory pregame jobs; optionally catch up after restarts."""
    now_utc = now_utc or datetime.now(UTC)
    today = datetime.now(ET).date()
    games = db.query(Game).filter(Game.game_date == today).all()
    result = {
        "games": len(games),
        "scheduled": 0,
        "catch_up_scheduled": 0,
        "already_scheduled": 0,
        "already_ready": 0,
        "past_start": 0,
        "missing_start_time": 0,
    }

    catch_up_offset_seconds = 5
    for game in games:
        game_dt = _parse_game_start_time(game)
        if game_dt is None:
            result["missing_start_time"] += 1
            continue

        job_id = f"pregame_{game.game_id}"
        if scheduler.get_job(job_id):
            result["already_scheduled"] += 1
            continue

        latest_pregame = get_latest_odds_snapshot(
            db,
            game_id=game.game_id,
            snapshot_type=SnapshotType.pregame,
        )
        if latest_pregame is not None and _pregame_processing_complete(db, game_id=game.game_id):
            result["already_ready"] += 1
            continue

        pregame_trigger_time = game_dt - timedelta(minutes=45)
        run_at = pregame_trigger_time
        is_catch_up = False
        if pregame_trigger_time <= now_utc:
            if not catch_up_missing or game_dt <= now_utc:
                result["past_start"] += 1
                continue
            run_at = now_utc + timedelta(seconds=catch_up_offset_seconds)
            catch_up_offset_seconds += 10
            is_catch_up = True

        scheduler.add_job(
            run_pregame_snapshot,
            trigger=DateTrigger(run_date=run_at),
            args=[game.game_id],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=600,
        )
        if is_catch_up:
            result["catch_up_scheduled"] += 1
        else:
            result["scheduled"] += 1
        logger.info(
            "[scheduler] Pregame job scheduled for game %s at %s catch_up=%s",
            game.game_id,
            run_at,
            is_catch_up,
        )
    return result


def _recent_pregame_board_rows(
    db,
    *,
    game_id: int,
    now_utc: datetime | None = None,
    max_age_minutes: int = PREGAME_BOARD_REUSE_WINDOW_MINUTES,
) -> list[GameOdds]:
    """Return a recently fetched pregame board if it includes the target game."""
    now_utc = now_utc or datetime.now(UTC)
    latest_for_game = get_latest_odds_snapshot(
        db,
        game_id=game_id,
        snapshot_type=SnapshotType.pregame,
    )
    if latest_for_game is None or not is_odds_snapshot_fresh(
        latest_for_game,
        now=now_utc,
        max_age_minutes=max_age_minutes,
    ):
        return []

    return (
        db.query(GameOdds)
        .filter(
            GameOdds.snapshot_type == SnapshotType.pregame,
            GameOdds.fetched_at == latest_for_game.fetched_at,
        )
        .all()
    )


def _pregame_processing_complete(db, *, game_id: int) -> bool:
    movement = db.query(LineMovement).filter(LineMovement.game_id == game_id).first()
    edge = (
        db.query(EdgeResult)
        .filter(
            EdgeResult.game_id == game_id,
            EdgeResult.is_active.is_(True),
            EdgeResult.run_stage == "pregame",
        )
        .order_by(EdgeResult.calculated_at.desc(), EdgeResult.id.desc())
        .first()
    )
    return movement is not None and edge is not None


def _has_active_prediction(db, *, game_id: int, run_stage: str) -> bool:
    return (
        db.query(Prediction)
        .filter(
            Prediction.game_id == game_id,
            Prediction.run_stage == run_stage,
            Prediction.is_active.is_(True),
        )
        .first()
        is not None
    )


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


@scheduler.scheduled_job(CronTrigger(hour=9, minute=15, timezone="America/New_York"))
def collect_bullpen_workload_job():
    db = SessionLocal()
    try:
        today = datetime.now(ET).date()
        yesterday = today - timedelta(days=1)
        three_days_ago = today - timedelta(days=3)

        # Find all teams that played in the last 3 days
        recent_games = (
            db.query(Game)
            .filter(Game.game_date >= three_days_ago, Game.game_date <= yesterday)
            .all()
        )
        team_ids: set[int] = set()
        for g in recent_games:
            if g.home_team_id:
                team_ids.add(g.home_team_id)
            if g.away_team_id:
                team_ids.add(g.away_team_id)

        total_rows = 0
        for team_id in team_ids:
            n = collect_reliever_workload(team_id, today, db)
            total_rows += n

        # Update manager tendencies for yesterday's completed games
        yesterday_games = db.query(Game).filter(Game.game_date == yesterday).all()
        for g in yesterday_games:
            for team_id in filter(None, [g.home_team_id, g.away_team_id]):
                track_manager_decision(team_id, g.game_id, db)

        print(
            f"[scheduler] Bullpen workload: {total_rows} reliever rows across "
            f"{len(team_ids)} teams; {len(yesterday_games)} manager updates"
        )
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.exception("[scheduler] Bullpen workload error")
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

        schedule_result = schedule_pregame_jobs_for_today(db)
        print(f"[scheduler] Pregame jobs: {schedule_result}")
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
        elif stored := _recent_pregame_board_rows(db, game_id=game_id):
            print(
                f"[scheduler] Pregame board reused for game {game_id}: "
                f"rows={len(stored)} fetched_at={stored[0].fetched_at}"
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

        if not _has_active_prediction(db, game_id=game_id, run_stage="pregame"):
            game = db.query(Game).filter(Game.game_id == game_id).first()
            target_date = game.game_date if game else datetime.now(ET).date()
            prediction_result = run_predictions_for_date(
                db,
                target_date,
                run_stage="pregame",
                diagnostic_label="scheduler-pregame",
            )
            print(f"[scheduler] Pregame predictions refreshed: {prediction_result}")

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
        old_result = get_latest_calibration_result(db)
        old_accuracy = float(old_result.accuracy) if old_result else None
        result = run_logistic_regression(db, [2022, 2023, 2024], apply_weights=False)
        new_accuracy = float(result.accuracy)
        deployed = old_accuracy is None or new_accuracy > old_accuracy

        if deployed:
            apply_backtest_weights(result)
        else:
            from app.services.notification_service import send_alert_message

            result_id = result.id
            db.delete(result)
            db.commit()
            send_alert_message(
                "Weekly backtest kept existing model weights: "
                f"new accuracy {new_accuracy:.4f} did not beat old accuracy {old_accuracy:.4f}. "
                f"Candidate result {result_id} was discarded."
            )

        print(
            f"[scheduler] Weekly backtest: seasons={result.seasons}, "
            f"n_games={result.n_games}, accuracy={result.accuracy:.4f}, "
            f"cv={result.cv_accuracy:.4f}, brier={result.brier_score:.4f}, "
            f"calibrated={result.calibration_params_json is not None}, "
            f"old_accuracy={old_accuracy}, deployed={deployed}"
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
