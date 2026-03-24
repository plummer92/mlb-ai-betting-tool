from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.db import SessionLocal
from app.models.schema import Game, SnapshotType
from app.services.edge_service import calculate_edge_for_game
from app.services.odds_service import compute_line_movement, fetch_and_store_odds

scheduler = AsyncIOScheduler(timezone="America/New_York")


# ── Job 1: Morning open snapshot ────────────────────────────────────────────
# Runs at 10:00 AM ET every day. Grabs opening lines for all today's games
# in a single API call.
@scheduler.scheduled_job(CronTrigger(hour=10, minute=0, timezone="America/New_York"))
async def morning_odds_snapshot():
    db = SessionLocal()
    try:
        stored = await fetch_and_store_odds(db, snapshot_type=SnapshotType.open)
        print(f"[scheduler] Morning snapshot: {len(stored)} odds rows stored")
    finally:
        db.close()


# ── Job 2: Schedule pre-game snapshots ──────────────────────────────────────
# Runs at 10:15 AM ET (after games are synced and morning odds stored).
# For each game today, schedules a one-time job 45 min before first pitch.
@scheduler.scheduled_job(CronTrigger(hour=10, minute=15, timezone="America/New_York"))
async def schedule_pregame_jobs():
    db = SessionLocal()
    try:
        today = datetime.now(ZoneInfo("America/New_York")).date()
        games = db.query(Game).filter(Game.game_date == today).all()

        for game in games:
            if not game.start_time:
                continue

            try:
                # start_time is stored as an ISO string from mlb_api.py
                game_dt = datetime.fromisoformat(game.start_time)
                if game_dt.tzinfo is None:
                    game_dt = game_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                print(f"[scheduler] Could not parse start_time for game {game.game_id}: {game.start_time}")
                continue

            pregame_trigger_time = game_dt - timedelta(minutes=45)

            if pregame_trigger_time <= datetime.now(timezone.utc):
                continue  # game is too soon or already started

            job_id = f"pregame_{game.game_id}"
            if scheduler.get_job(job_id):
                continue  # already scheduled

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
    """
    Fired 45 min before each game.
    1. Fetch pregame odds snapshot (one API call covers all today's games)
    2. Compute line movement for this game
    3. Recalculate edge with movement signal factored in
    """
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
    finally:
        db.close()
