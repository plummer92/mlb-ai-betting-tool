import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.db import SessionLocal
from app.models.schema import Game
from app.services.alert_service import create_and_send_alert_for_game, create_and_send_alerts_for_today
from app.services.backtest_service import (
    apply_calibration,
    build_live_feature_vector,
    get_latest_calibration_result,
    run_logistic_regression,
    score_logistic_home_probability,
)
from app.services.edge_service import calculate_all_edges_today, calculate_edge_for_game
from app.services.feature_builder import build_team_features
from app.services.model_diagnostics import summarize_edge_diagnostics, summarize_probability_diagnostics
from app.services.mlb_api import fetch_bullpen_stats, fetch_pitcher_stats, fetch_schedule_for_date, fetch_team_stats
from app.services.odds_service import (
    SnapshotType,
    compute_line_movement,
    fetch_and_store_odds,
    get_latest_odds_snapshot,
    get_market_home_probability,
    is_odds_snapshot_fresh,
)
from app.services.prediction_service import deactivate_stale_active_predictions, store_prediction
from app.services.ranked_alerts import send_ranked_bets_to_discord_job
from app.services.review_service import resolve_completed_games
from app.services.simulator import MODEL_VERSION, run_monte_carlo
from app.services.statcast_service import fetch_team_statcast

scheduler = AsyncIOScheduler(timezone="America/New_York")
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


# ── 9:00 AM ET: resolve any unresolved games from yesterday ─────────────────
@scheduler.scheduled_job(CronTrigger(hour=9, minute=0, timezone="America/New_York"))
def resolve_yesterday_job():
    db = SessionLocal()
    try:
        result = resolve_completed_games(db)
        print(f"[scheduler] 9am resolve: {result}")
    except Exception as e:
        print(f"[scheduler] Resolve error: {e}")
    finally:
        db.close()


# ── 9:30 AM ET: sync today's schedule from the MLB Stats API ────────────────
@scheduler.scheduled_job(CronTrigger(hour=9, minute=30, timezone="America/New_York"))
def sync_today_games_job():
    db = SessionLocal()
    try:
        today = datetime.now(ET).date()
        games = fetch_schedule_for_date(str(today))
        synced = 0
        for g in games:
            existing = db.query(Game).filter(Game.game_id == g["game_id"]).first()
            if existing:
                existing.status = g["status"]
                existing.start_time = g["start_time"]
                existing.away_probable_pitcher = g["away_probable_pitcher"]
                existing.away_pitcher_id = g["away_pitcher_id"]
                existing.home_probable_pitcher = g["home_probable_pitcher"]
                existing.home_pitcher_id = g["home_pitcher_id"]
                existing.final_away_score = g["final_away_score"]
                existing.final_home_score = g["final_home_score"]
            else:
                db.add(Game(
                    game_id=g["game_id"],
                    game_date=date.fromisoformat(g["game_date"]),
                    season=g["season"],
                    away_team=g["away_team"],
                    home_team=g["home_team"],
                    away_team_id=g["away_team_id"],
                    home_team_id=g["home_team_id"],
                    venue=g["venue"],
                    status=g["status"],
                    start_time=g["start_time"],
                    away_probable_pitcher=g["away_probable_pitcher"],
                    away_pitcher_id=g["away_pitcher_id"],
                    home_probable_pitcher=g["home_probable_pitcher"],
                    home_pitcher_id=g["home_pitcher_id"],
                    final_away_score=g["final_away_score"],
                    final_home_score=g["final_home_score"],
                ))
                synced += 1
        db.commit()
        print(f"[scheduler] Game sync: {len(games)} total, {synced} new")
    except Exception as e:
        db.rollback()
        print(f"[scheduler] Game sync error: {e}")
    finally:
        db.close()


# ── 10:00 AM ET: fetch opening odds snapshot ────────────────────────────────
@scheduler.scheduled_job(CronTrigger(hour=10, minute=0, timezone="America/New_York"))
async def morning_odds_snapshot():
    db = SessionLocal()
    try:
        stored = await fetch_and_store_odds(db, snapshot_type=SnapshotType.open)
        print(f"[scheduler] Morning snapshot: {len(stored)} odds rows stored")
    except Exception as e:
        print(f"[scheduler] Morning odds error: {e}")
    finally:
        db.close()


# ── 10:15 AM ET: Monte Carlo for all games + schedule per-game pregame jobs ─
@scheduler.scheduled_job(CronTrigger(hour=10, minute=15, timezone="America/New_York"))
def run_monte_carlo_and_schedule_pregame():
    db = SessionLocal()
    try:
        today = datetime.now(ET).date()
        deactivated = deactivate_stale_active_predictions(db, keep_on_or_after=today)
        if deactivated:
            print(f"[scheduler] Prediction cleanup: deactivated={deactivated}")
        games = db.query(Game).filter(Game.game_date == today).all()
        cal_result = get_latest_calibration_result(db)
        cal_params = json.loads(cal_result.calibration_params_json) if cal_result and cal_result.calibration_params_json else None
        ok, err = [], []
        probability_results = []
        for game in games:
            try:
                away_raw = fetch_team_stats(game.away_team_id, game.season)
                home_raw = fetch_team_stats(game.home_team_id, game.season)
                away_starter = fetch_pitcher_stats(game.away_pitcher_id, game.season, include_xera=True) if game.away_pitcher_id else None
                home_starter = fetch_pitcher_stats(game.home_pitcher_id, game.season, include_xera=True) if game.home_pitcher_id else None
                away_bullpen = fetch_bullpen_stats(game.away_team_id, game.season)
                home_bullpen = fetch_bullpen_stats(game.home_team_id, game.season)
                away_statcast = fetch_team_statcast(game.away_team_id, game.season)
                home_statcast = fetch_team_statcast(game.home_team_id, game.season)
                away_features = build_team_features(
                    away_raw,
                    starter_stats=away_starter,
                    bullpen_stats=away_bullpen,
                    statcast_team=away_statcast,
                )
                home_features = build_team_features(
                    home_raw,
                    starter_stats=home_starter,
                    venue=game.venue,
                    bullpen_stats=home_bullpen,
                    statcast_team=home_statcast,
                )
                latest_open = get_latest_odds_snapshot(db, game_id=game.game_id, snapshot_type=SnapshotType.open)
                market_home_prob = get_market_home_probability(latest_open) if latest_open and is_odds_snapshot_fresh(latest_open) else None
                logistic_home_prob = score_logistic_home_probability(
                    build_live_feature_vector(home_features, away_features),
                    cal_result,
                )
                result = run_monte_carlo(
                    away_team=away_features,
                    home_team=home_features,
                    sim_count=1000,
                    market_home_prob=market_home_prob,
                    logistic_home_prob=logistic_home_prob,
                )
                raw_home = result["home_win_pct"]
                raw_away = result["away_win_pct"]
                cal_home = cal_away = None
                if cal_params:
                    cal_home, cal_away = apply_calibration(raw_home, raw_away, cal_params)
                store_prediction(
                    db,
                    game_id=game.game_id,
                    model_version=MODEL_VERSION,
                    run_stage="daily_open",
                    sim_count=result["sim_count"],
                    away_win_pct=raw_away,
                    home_win_pct=raw_home,
                    calibrated_home_win_pct=cal_home,
                    calibrated_away_win_pct=cal_away,
                    projected_away_score=result["projected_away_score"],
                    projected_home_score=result["projected_home_score"],
                    projected_total=result["projected_total"],
                    confidence_score=result["confidence_score"],
                    recommended_side=result["recommended_side"],
                    home_starter_xera=home_features.get("starter_xera"),
                    away_starter_xera=away_features.get("starter_xera"),
                    using_xera=bool(home_features.get("using_xera") or away_features.get("using_xera")),
                    calibration_result_id=cal_result.id if cal_result else None,
                )
                ok.append(game.game_id)
                probability_results.append(result)
            except Exception as e:
                db.rollback()
                err.append({"game_id": game.game_id, "error": str(e)})
        print(f"[scheduler] Monte Carlo: {len(ok)} ok, {len(err)} errors")
        summarize_probability_diagnostics(probability_results, label="scheduler-daily-open")

        # Schedule per-game pregame jobs (T-45 min before first pitch)
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
    except Exception as e:
        print(f"[scheduler] Monte Carlo job error: {e}")
    finally:
        db.close()


# ── 10:30 AM ET: calculate edges for all today's games ──────────────────────
@scheduler.scheduled_job(CronTrigger(hour=10, minute=30, timezone="America/New_York"))
async def calculate_edges_job():
    db = SessionLocal()
    try:
        stored = await fetch_and_store_odds(db, snapshot_type=SnapshotType.open)
        results = calculate_all_edges_today(
            db,
            run_stage="daily_open",
            snapshot_type=SnapshotType.open,
            odds_rows=stored,
            fallback_policy="reuse_fresh_same_stage",
        )
        created = sum(1 for row in results if row["status"] == "created")
        skipped = {}
        for row in results:
            if row["status"] != "created":
                skipped[row["reason"]] = skipped.get(row["reason"], 0) + 1
        summarize_edge_diagnostics(results, label="scheduler-daily-open")
        print(f"[scheduler] Edges calculated: {created} created, skipped={skipped}")
    except Exception as e:
        print(f"[scheduler] Edge calculation error: {e}")
    finally:
        db.close()


# ── 10:45 AM ET: send alerts for qualifying edges ───────────────────────────
@scheduler.scheduled_job(CronTrigger(hour=10, minute=45, timezone="America/New_York"))
def send_morning_alerts_job():
    db = SessionLocal()
    try:
        result = create_and_send_alerts_for_today(db)
        print(f"[scheduler] Morning alerts: {result}")
    except Exception as e:
        print(f"[scheduler] Alert error: {e}")
    finally:
        db.close()


# ── Per-game pregame snapshot: odds + movement + edge + alert (T-45 min) ────
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
        # Alert for this game only — deduped on (game_id, edge_result_id)
        alert_result = create_and_send_alert_for_game(db, game_id)
        print(f"[scheduler] Pregame alert for game {game_id}: {alert_result}")
    except Exception as e:
        print(f"[scheduler] Pregame snapshot error for game {game_id}: {e}")
    finally:
        db.close()


# ── Every 15 min, 3pm–midnight ET: resolve completed games ──────────────────
@scheduler.scheduled_job(CronTrigger(hour="15-23", minute="*/15", timezone="America/New_York"))
def resolve_completed_games_job():
    db = SessionLocal()
    try:
        result = resolve_completed_games(db)
        print(f"[scheduler] Postgame resolver: {result}")
    except Exception as e:
        print(f"[scheduler] Postgame resolve error: {e}")
    finally:
        db.close()


# ── Every Monday 6am ET: re-run backtest regression, update simulator weights
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
    except Exception as e:
        print(f"[scheduler] Weekly backtest failed: {e}")
    finally:
        db.close()


# ── 11:00 AM ET daily: send ranked bets summary board to Discord ─────────────
@scheduler.scheduled_job(CronTrigger(hour=11, minute=0, timezone="America/New_York"))
def ranked_bets_discord_job():
    try:
        result = send_ranked_bets_to_discord_job(limit=10, active_only=True)
        print(f"[scheduler] Discord summary: {result}")
    except Exception as e:
        print(f"[scheduler] Discord summary error: {e}")
