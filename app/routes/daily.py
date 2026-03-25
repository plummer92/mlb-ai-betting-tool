from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime, date
from zoneinfo import ZoneInfo

from app.db import get_db
from app.models.schema import Game, Prediction
from app.services.mlb_api import fetch_schedule_for_date, fetch_team_stats, fetch_pitcher_stats
from app.services.feature_builder import build_team_features
from app.services.simulator import run_monte_carlo
from app.services.odds_service import fetch_and_store_odds, compute_line_movement, SnapshotType
from app.services.edge_service import calculate_all_edges_today

router = APIRouter(prefix="/api", tags=["daily"])


@router.post("/daily-run")
async def daily_run(db: Session = Depends(get_db)):
    et = ZoneInfo("America/New_York")
    today = datetime.now(et).date()
    results = {"date": str(today), "steps": {}}

    # Step 1 - sync games
    try:
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
        results["steps"]["sync_games"] = {"status": "ok", "total": len(games), "new": synced}
    except Exception as e:
        results["steps"]["sync_games"] = {"status": "error", "detail": str(e)}

    # Step 2 - run Monte Carlo for each game
    game_records = db.query(Game).filter(Game.game_date == today).all()
    mc_ok = []
    mc_err = []
    for game in game_records:
        try:
            away_raw = fetch_team_stats(team_id=game.away_team_id, season=game.season)
            home_raw = fetch_team_stats(team_id=game.home_team_id, season=game.season)
            away_starter = fetch_pitcher_stats(game.away_pitcher_id, game.season) if game.away_pitcher_id else None
            home_starter = fetch_pitcher_stats(game.home_pitcher_id, game.season) if game.home_pitcher_id else None
            away_features = build_team_features(away_raw, starter_stats=away_starter)
            home_features = build_team_features(home_raw, starter_stats=home_starter)
            result = run_monte_carlo(away_team=away_features, home_team=home_features, sim_count=1000)
            prediction = Prediction(
                game_id=game.game_id,
                model_version="v0.1-neon",
                sim_count=result["sim_count"],
                away_win_pct=result["away_win_pct"],
                home_win_pct=result["home_win_pct"],
                projected_away_score=result["projected_away_score"],
                projected_home_score=result["projected_home_score"],
                projected_total=result["projected_total"],
                confidence_score=result["confidence_score"],
                recommended_side=result["recommended_side"],
            )
            db.add(prediction)
            db.commit()
            mc_ok.append(game.game_id)
        except Exception as e:
            mc_err.append({"game_id": game.game_id, "error": str(e)})
    results["steps"]["monte_carlo"] = {
        "status": "ok" if not mc_err else "partial",
        "ran": len(mc_ok),
        "errors": mc_err
    }

    # Step 3 - sync opening odds
    try:
        stored = await fetch_and_store_odds(db, snapshot_type=SnapshotType.open)
        results["steps"]["sync_odds"] = {"status": "ok", "stored": len(stored)}
    except Exception as e:
        results["steps"]["sync_odds"] = {"status": "error", "detail": str(e)}

    # Step 4 - calculate edges
    try:
        edges = calculate_all_edges_today(db)
        results["steps"]["edges"] = {"status": "ok", "calculated": len(edges)}
    except Exception as e:
        results["steps"]["edges"] = {"status": "error", "detail": str(e)}

    return results


@router.post("/pregame-run")
async def pregame_run(db: Session = Depends(get_db)):
    """
    Run ~45 min before first pitch:
    1. Fetch pregame odds snapshot.
    2. Compute line movement (open vs pregame) for each game.
    3. Recalculate edges with movement signal baked in.
    """
    et = ZoneInfo("America/New_York")
    today = datetime.now(et).date()
    results = {"date": str(today), "steps": {}}

    # Step 1 — pregame odds snapshot
    try:
        stored = await fetch_and_store_odds(db, snapshot_type=SnapshotType.pregame)
        results["steps"]["sync_pregame_odds"] = {"status": "ok", "stored": len(stored)}
    except Exception as e:
        results["steps"]["sync_pregame_odds"] = {"status": "error", "detail": str(e)}

    # Step 2 — line movement
    game_records = db.query(Game).filter(Game.game_date == today).all()
    mv_ok, mv_err = [], []
    for game in game_records:
        try:
            movement = compute_line_movement(db, game.game_id)
            if movement:
                mv_ok.append(game.game_id)
        except Exception as e:
            mv_err.append({"game_id": game.game_id, "error": str(e)})
    results["steps"]["line_movement"] = {
        "status": "ok" if not mv_err else "partial",
        "computed": len(mv_ok),
        "errors": mv_err,
    }

    # Step 3 — recalculate edges with movement signal
    try:
        edges = calculate_all_edges_today(db)
        results["steps"]["edges"] = {"status": "ok", "calculated": len(edges)}
    except Exception as e:
        results["steps"]["edges"] = {"status": "error", "detail": str(e)}

    return results
