from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.db import SessionLocal
from app.models.schema import Game, SnapshotType
from app.services.alert_service import create_and_send_alerts_for_today
from app.services.edge_service import calculate_edge_for_game
from app.services.odds_service import compute_line_movement, fetch_and_store_odds
from app.services.review_service import resolve_completed_games

scheduler = AsyncIOScheduler(timezone="America/New_York")
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


@scheduler.scheduled_job(CronTrigger(hour=10, minute=0, timezone="America/New_York"))
async def morning_odds_snapshot():
    db = SessionLocal()
    try:
        stored = await fetch_and_store_odds(db, snapshot_type=SnapshotType.open)
        print(f"[scheduler] Morning snapshot: {len(stored)} odds rows stored")
    finally:
        db.close()


@scheduler.scheduled_job(CronTrigger(hour=10, minute=15, timezone="America/New_York"))
async def schedule_pregame_jobs():
    db = SessionLocal()
    try:
        today = datetime.now(ET).date()
        games = db.query(Game).filter(Game.game_date == today).all()

        for game in games:
            if not game.start_time:
                continue

            try:
                game_dt = datetime.fromisoformat(game.start_time)
                if game_dt.tzinfo is None:
                    game_dt = game_dt.replace(tzinfo=UTC)
            except ValueError:
                print(f"[scheduler] Could not parse start_time for game {game.game_id}: {game.start_time}")
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
            print(f"[scheduler] Pregame job scheduled for game {game.game_id} at {pregame_trigger_time}")
    finally:
        db.close()


async def run_pregame_snapshot(game_id: int):
    db = SessionLocal()
    try:
        stored = await fetch_and_store_odds(db, snapshot_type=SnapshotType.pregame)
        print(f"[scheduler] Pregame snapshot stored: {len(stored)} rows")

        movement = compute_line_movement(db, game_id)
        if movement:
            print(
                f"[scheduler] Game {game_id} — "
                f"away move: {movement.away_prob_move:+.3f}, "
                f"home move: {movement.home_prob_move:+.3f}, "
                f"sharp_away={movement.sharp_away}, "
                f"sharp_home={movement.sharp_home}"
            )

        calculate_edge_for_game(db, game_id, movement=movement)
        print(f"[scheduler] Edge recalculated for game {game_id}")

        alert_result = create_and_send_alerts_for_today(db)
        print(f"[scheduler] Alerts: {alert_result}")
    finally:
        db.close()


@scheduler.scheduled_job(CronTrigger(minute="*/15", timezone="America/New_York"))
def resolve_completed_games_job():
    db = SessionLocal()
    try:
        result = resolve_completed_games(db)
        print(f"[scheduler] Postgame resolver: {result}")
    finally:
        db.close()
