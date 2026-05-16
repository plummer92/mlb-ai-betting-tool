from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.playbyplay_simulator import (
    compare_sim_to_actual,
    fetch_actual_play_by_play,
    simulate_play_by_play,
)

router = APIRouter(prefix="/api/playbyplay", tags=["playbyplay"])


@router.get("/simulate/{game_id}")
def simulate_game(game_id: int, db: Session = Depends(get_db)):
    return simulate_play_by_play(db, game_id)


@router.get("/actual/{game_id}")
def actual_game(game_id: int):
    return fetch_actual_play_by_play(game_id)


@router.get("/compare/{game_id}")
def compare_game(game_id: int, db: Session = Depends(get_db)):
    return compare_sim_to_actual(db, game_id)
