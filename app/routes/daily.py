"""
POST /api/daily-run

Composite endpoint that chains the full morning pipeline in one call:
  1. sync-today       — pull today's MLB schedule
  2. run/all-today    — Monte Carlo for every game
  3. sync-odds (open) — grab opening lines from sportsbooks
  4. calculate-all    — compute edges

This is the single cron target and the "run from your phone" button.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import Game, SnapshotType
from app.routes.model import _run_model_for_game
from app.services.edge_service import calculate_all_edges_today
from app.services.feature_builder import is_dome_venue, weather_run_modifier
from app.services.mlb_api import fetch_all_team_records, fetch_schedule_for_date
from app.services.odds_service import fetch_and_store_odds

from datetime import date as date_type

router = APIRouter(prefix="/api", tags=["daily"])


@router.post("/daily-run")
async def daily_run(db: Session = Depends(get_db)):
    """
    Full morning pipeline in one call.
    Safe to re-run — each step is idempotent (upserts, not blind inserts).
    """
    today_et = datetime.now(ZoneInfo("America/New_York")).date()
    summary: dict = {"date": str(today_et), "steps": {}}

    # ── Step 1: Sync today's games ───────────────────────────────────────────
    today_str = today_et.isoformat()
    games_data = fetch_schedule_for_date(today_str)
    synced, updated = 0, 0
    for g in games_data:
        existing = db.query(Game).filter(Game.game_id == g["game_id"]).first()
        if existing:
            existing.status               = g["status"]
            existing.start_time           = g["start_time"]
            existing.venue                = g["venue"]
            existing.away_probable_pitcher = g["away_probable_pitcher"]
            existing.home_probable_pitcher = g["home_probable_pitcher"]
            existing.final_away_score     = g["final_away_score"]
            existing.final_home_score     = g["final_home_score"]
            if g["weather_condition"] is not None:
                existing.weather_condition = g["weather_condition"]
                existing.weather_temp      = g["weather_temp"]
                existing.weather_wind      = g["weather_wind"]
                existing.weather_wind_mph  = g["weather_wind_mph"]
                existing.weather_wind_dir  = g["weather_wind_dir"]
            updated += 1
        else:
            db.add(Game(
                game_id=g["game_id"],
                game_date=date_type.fromisoformat(g["game_date"]),
                season=g["season"],
                away_team=g["away_team"],
                home_team=g["home_team"],
                away_team_id=g["away_team_id"],
                home_team_id=g["home_team_id"],
                venue=g["venue"],
                status=g["status"],
                start_time=g["start_time"],
                away_probable_pitcher=g["away_probable_pitcher"],
                home_probable_pitcher=g["home_probable_pitcher"],
                final_away_score=g["final_away_score"],
                final_home_score=g["final_home_score"],
                weather_condition=g["weather_condition"],
                weather_temp     =g["weather_temp"],
                weather_wind     =g["weather_wind"],
                weather_wind_mph =g["weather_wind_mph"],
                weather_wind_dir =g["weather_wind_dir"],
            ))
            synced += 1
    db.commit()
    summary["steps"]["sync_games"] = {"new": synced, "updated": updated, "total": synced + updated}

    # ── Step 2: Run Monte Carlo for all today's games ────────────────────────
    games = db.query(Game).filter(Game.game_date == today_et).all()
    if games:
        team_records = fetch_all_team_records(season=games[0].season)
        ran, skipped = 0, 0
        model_results = []
        for game in games:
            if not game.away_team_id or not game.home_team_id:
                skipped += 1
                continue
            try:
                pred = _run_model_for_game(game, team_records, db)
                model_results.append({
                    "game_id": game.game_id,
                    "matchup": f"{game.away_team} @ {game.home_team}",
                    "away_win_pct": pred.away_win_pct,
                    "home_win_pct": pred.home_win_pct,
                    "projected_total": pred.projected_total,
                    "weather_modifier": weather_run_modifier(
                        game.weather_temp, game.weather_wind_mph,
                        game.weather_wind_dir, is_dome_venue(game.venue),
                    ),
                })
                ran += 1
            except Exception as exc:
                skipped += 1
                model_results.append({"game_id": game.game_id, "error": str(exc)})
        summary["steps"]["model"] = {"ran": ran, "skipped": skipped, "results": model_results}
    else:
        summary["steps"]["model"] = {"ran": 0, "skipped": 0, "results": []}

    # ── Step 3: Sync opening odds ────────────────────────────────────────────
    stored_odds = await fetch_and_store_odds(db, snapshot_type=SnapshotType.open)
    summary["steps"]["sync_odds"] = {"stored": len(stored_odds), "snapshot_type": "open"}

    # ── Step 4: Calculate edges ──────────────────────────────────────────────
    edge_results = calculate_all_edges_today(db)
    summary["steps"]["edges"] = {
        "calculated": len(edge_results),
        "plays": [
            {
                "game_id": e.game_id,
                "recommended_play": e.recommended_play,
                "confidence_tier": e.confidence_tier,
                "edge_pct": float(e.edge_pct) if e.edge_pct else None,
            }
            for e in edge_results if e.recommended_play
        ],
    }

    return summary
