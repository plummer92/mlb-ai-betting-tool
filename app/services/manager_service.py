"""
Manager tendency tracking and bullpen fatigue reporting.

All functions catch exceptions silently and return safe defaults.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.schema import Game, ManagerTendency, RelieverWorkload
from app.services.bullpen_calc import (
    _DEFAULT_B2B,
    get_team_bullpen_availability,
    invalidate_manager_cache,
)

logger = logging.getLogger(__name__)

_DEFAULT_AVG_RELIEVERS = 3.5
_DEFAULT_AVG_PITCHES = 70.0
_ROLLING_N = 30.0


def track_manager_decision(team_id: int, game_id: int, db: Session) -> None:
    """
    After a completed game, compute bullpen usage metrics and update the
    rolling 30-game averages in manager_tendencies.  Never raises.
    """
    try:
        game = db.query(Game).filter(Game.game_id == game_id).first()
        if not game or game.game_date is None:
            return

        game_date = game.game_date

        rows = (
            db.query(RelieverWorkload)
            .filter(
                RelieverWorkload.team_id == team_id,
                RelieverWorkload.date == game_date,
            )
            .all()
        )
        if not rows:
            return

        relievers_used = len(rows)
        total_bullpen_pitches = sum(r.pitches_thrown or 0 for r in rows)
        b2b_count = sum(1 for r in rows if (r.days_rest or 99) == 0)
        b2b_usage = b2b_count / max(relievers_used, 1)

        manager = db.query(ManagerTendency).filter(ManagerTendency.team_id == team_id).first()
        if not manager:
            manager = ManagerTendency(team_id=team_id, b2b_usage_rate=_DEFAULT_B2B)
            db.add(manager)
            db.flush()

        n = _ROLLING_N
        manager.b2b_usage_rate = (manager.b2b_usage_rate or _DEFAULT_B2B) * (n - 1) / n + b2b_usage / n
        manager.avg_relievers_per_game = (
            (manager.avg_relievers_per_game or _DEFAULT_AVG_RELIEVERS) * (n - 1) / n
            + relievers_used / n
        )
        manager.avg_bullpen_pitches_per_game = (
            (manager.avg_bullpen_pitches_per_game or _DEFAULT_AVG_PITCHES) * (n - 1) / n
            + total_bullpen_pitches / n
        )

        db.commit()
        invalidate_manager_cache(team_id)
        logger.debug(
            "[manager] team=%d game=%d relievers=%d b2b_usage=%.2f total_pitches=%d",
            team_id, game_id, relievers_used, b2b_usage, total_bullpen_pitches,
        )
    except Exception:
        db.rollback()
        logger.exception("[manager] track_manager_decision silenced error team=%d game=%d", team_id, game_id)


def get_bullpen_fatigue_report(team_id: int, db: Session) -> dict:
    """
    Returns a fatigue report dict for a team.  Falls back to neutral values on any error.
    """
    try:
        target_date = date.today()
        three_days_ago = target_date - timedelta(days=3)

        # Resolve team name from most recent game
        game = (
            db.query(Game)
            .filter(
                (Game.home_team_id == team_id) | (Game.away_team_id == team_id)
            )
            .order_by(Game.game_date.desc())
            .first()
        )
        if game:
            team_name = game.home_team if game.home_team_id == team_id else game.away_team
        else:
            team_name = f"Team {team_id}"

        # All workload rows for this team in the last 3 days
        rows = (
            db.query(RelieverWorkload)
            .filter(
                RelieverWorkload.team_id == team_id,
                RelieverWorkload.date >= three_days_ago,
                RelieverWorkload.date < target_date,
            )
            .all()
        )

        last_3_days_pitches = sum(r.pitches_thrown or 0 for r in rows)

        # Per-player summary: latest appearance per player
        player_pitches: dict[int, int] = {}
        player_latest: dict[int, date] = {}
        player_name_map: dict[int, str] = {}
        for r in rows:
            if r.player_id is None:
                continue
            player_pitches[r.player_id] = player_pitches.get(r.player_id, 0) + (r.pitches_thrown or 0)
            if r.player_id not in player_latest or r.date > player_latest[r.player_id]:
                player_latest[r.player_id] = r.date
                player_name_map[r.player_id] = r.player_name or f"Player {r.player_id}"

        fatigued_arms = []
        fresh_arms = []
        for pid, last_date in player_latest.items():
            days_rest = (target_date - last_date).days
            name = player_name_map[pid]
            pitches = player_pitches[pid]
            if days_rest < 2:
                fatigued_arms.append({"name": name, "days_rest": days_rest, "pitches_last_3": pitches})
            elif days_rest >= 3:
                fresh_arms.append({"name": name, "days_rest": days_rest})

        fatigued_arms.sort(key=lambda x: x["days_rest"])
        fresh_arms.sort(key=lambda x: x["days_rest"], reverse=True)

        strength = get_team_bullpen_availability(team_id, target_date, db)

        if strength < 0.30:
            fatigue_signal = "exhausted"
        elif strength < 0.55:
            fatigue_signal = "tired"
        elif strength < 0.75:
            fatigue_signal = "rested"
        else:
            fatigue_signal = "fresh"

        manager_row = db.query(ManagerTendency).filter(ManagerTendency.team_id == team_id).first()
        manager_name = manager_row.manager_name if manager_row else "Unknown"
        b2b_rate = manager_row.b2b_usage_rate if manager_row and manager_row.b2b_usage_rate is not None else _DEFAULT_B2B

        return {
            "team_id": team_id,
            "team_name": team_name,
            "last_3_days_pitches": last_3_days_pitches,
            "fatigued_arms": fatigued_arms,
            "fresh_arms": fresh_arms[:3],
            "bullpen_strength": round(strength, 2),
            "manager_name": manager_name,
            "b2b_usage_rate": round(float(b2b_rate), 2),
            "fatigue_signal": fatigue_signal,
        }
    except Exception:
        logger.exception("[manager] get_bullpen_fatigue_report error team=%d", team_id)
        return {
            "team_id": team_id,
            "team_name": f"Team {team_id}",
            "last_3_days_pitches": 0,
            "fatigued_arms": [],
            "fresh_arms": [],
            "bullpen_strength": 1.0,
            "manager_name": "Unknown",
            "b2b_usage_rate": _DEFAULT_B2B,
            "fatigue_signal": "fresh",
        }
