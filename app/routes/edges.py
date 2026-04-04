from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.edge_service import get_trustworthy_active_edges
from app.services.mlb_api import (
    fetch_team_stats, 
    fetch_pitcher_stats, 
    fetch_bullpen_stats, 
)
from app.services.feature_builder import build_team_features
from app.services.backtest_service import build_live_feature_vector

ET = ZoneInfo("America/New_York")

router = APIRouter(prefix="/api/edges", tags=["edges"])


@router.get("/top")
def get_top_edges(
    limit: int = Query(default=10, le=100),
    include_all_dates: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    today = datetime.now(ET).date()
    rows = get_trustworthy_active_edges(
        db,
        game_date=None if include_all_dates else today,
    )

    latest_by_game = {}
    for edge, _game, _prediction, _odds in rows:
        if edge.game_id not in latest_by_game:
            latest_by_game[edge.game_id] = edge

    top_rows = sorted(
        latest_by_game.values(),
        key=lambda edge: float(edge.edge_pct or 0),
        reverse=True,
    )[:limit]

    return [
        {
            "game_id": r.game_id,
            "play": r.recommended_play,
            "edge_pct": float(r.edge_pct) if r.edge_pct is not None else None,
            "ev_away": float(r.ev_away) if r.ev_away is not None else None,
            "ev_home": float(r.ev_home) if r.ev_home is not None else None,
            "confidence": r.confidence_tier,
            "pitching_edge_score": float(r.pitching_edge_score) if getattr(r, "pitching_edge_score", None) is not None else None,
            "calculated_at": r.calculated_at.isoformat() if r.calculated_at else None,
        }
        for r in top_rows
    ]


@router.get("/history/top")
def get_top_edges_history(
    limit: int = Query(default=10, le=100),
    db: Session = Depends(get_db),
):
    return get_top_edges(limit=limit, include_all_dates=True, db=db)


@router.get("/today")
def get_today_edges(db: Session = Depends(get_db)):
    today = datetime.now(ET).date()
    trusted_rows = get_trustworthy_active_edges(db, game_date=today)
    latest_by_game = {}
    for edge, game, prediction, odds in trusted_rows:
        if edge.game_id not in latest_by_game:
            latest_by_game[edge.game_id] = (edge, game)
    
    results = []
    for edge, game in latest_by_game.values():
        # Fetch stats to build live feature vector
        away_raw = fetch_team_stats(team_id=game.away_team_id, season=game.season)
        home_raw = fetch_team_stats(team_id=game.home_team_id, season=game.season)
        away_starter = fetch_pitcher_stats(game.away_pitcher_id, game.season, include_xera=True) if game.away_pitcher_id else None
        home_starter = fetch_pitcher_stats(game.home_pitcher_id, game.season, include_xera=True) if game.home_pitcher_id else None
        away_bullpen = fetch_bullpen_stats(game.away_team_id, game.season)
        home_bullpen = fetch_bullpen_stats(game.home_team_id, game.season)

        away_features = build_team_features(
            away_raw,
            starter_stats=away_starter,
            bullpen_stats=away_bullpen,
            statcast_team={},
        )
        home_features = build_team_features(
            home_raw,
            starter_stats=home_starter,
            venue=game.venue,
            bullpen_stats=home_bullpen,
            statcast_team={},
        )
        
        features = build_live_feature_vector(home_features, away_features)

        results.append({
            "game_id": edge.game_id,
            "play": edge.recommended_play,
            "edge_pct": float(edge.edge_pct) if edge.edge_pct is not None else None,
            "ev_away": float(edge.ev_away) if edge.ev_away is not None else None,
            "ev_home": float(edge.ev_home) if edge.ev_home is not None else None,
            "ev_over": float(edge.ev_over) if edge.ev_over is not None else None,
            "ev_under": float(edge.ev_under) if edge.ev_under is not None else None,
            "confidence": edge.confidence_tier,
            "movement_direction": edge.movement_direction,
            "model_away_win_pct": float(edge.model_away_win_pct) if edge.model_away_win_pct is not None else None,
            "model_home_win_pct": float(edge.model_home_win_pct) if edge.model_home_win_pct is not None else None,
            "model_total": float(edge.model_total) if edge.model_total is not None else None,
            "book_total": float(edge.book_total) if edge.book_total is not None else None,
            "calculated_at": edge.calculated_at.isoformat() if edge.calculated_at else None,
            # Advanced Features
            "kbb_adv": float(features.get("kbb_adv", 0)),
            "pythagorean_win_pct_adv": float(features.get("pythagorean_win_pct_adv", 0)),
            "park_factor_adv": float(features.get("park_factor_adv", 0)),
        })
    return results
