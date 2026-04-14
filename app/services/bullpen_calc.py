"""
v0.4 Bullpen calculation service.

All functions catch exceptions silently and return neutral fallback values
so v3 pipeline is never affected.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

import requests
from sqlalchemy.orm import Session

from app.models.schema import ManagerTendency, RelieverWorkload

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

# In-memory cache: team_id -> ManagerTendency-like dict
_manager_cache: dict[int, dict] = {}

# Hardcoded manager seed data: team_id -> (name, b2b_usage_rate, strict_pitch_cap)
_MANAGER_SEEDS: dict[int, tuple[str, float, int]] = {
    # Aggressive
    108: ("Ron Washington", 0.42, 25),
    111: ("Alex Cora", 0.38, 30),
    139: ("Kevin Cash", 0.45, 25),
    # Conservative
    117: ("Joe Espada", 0.18, 35),
    143: ("Rob Thomson", 0.15, 35),
    158: ("Pat Murphy", 0.20, 30),
}
_DEFAULT_B2B = 0.30
_DEFAULT_CAP = 30


def seed_manager_tendencies(db: Session) -> None:
    """Seed manager_tendencies table. Never overwrites existing rows."""
    seeded = 0
    for team_id, (name, b2b, cap) in _MANAGER_SEEDS.items():
        existing = db.query(ManagerTendency).filter(ManagerTendency.team_id == team_id).first()
        if existing:
            continue
        db.add(ManagerTendency(
            team_id=team_id,
            manager_name=name,
            b2b_usage_rate=b2b,
            strict_pitch_cap=cap,
        ))
        seeded += 1
    try:
        db.commit()
    except Exception:
        db.rollback()
    print(f"[v4 bullpen] Seeded {seeded} manager tendencies")


def _get_manager(team_id: int, db: Session) -> dict:
    """Return manager tendency dict for a team, with defaults if not found."""
    if team_id in _manager_cache:
        return _manager_cache[team_id]
    row = db.query(ManagerTendency).filter(ManagerTendency.team_id == team_id).first()
    if row:
        result = {
            "b2b_usage_rate": row.b2b_usage_rate or _DEFAULT_B2B,
            "strict_pitch_cap": row.strict_pitch_cap or _DEFAULT_CAP,
        }
    else:
        result = {"b2b_usage_rate": _DEFAULT_B2B, "strict_pitch_cap": _DEFAULT_CAP}
    _manager_cache[team_id] = result
    return result


def collect_reliever_workload(team_id: int, target_date: date, db: Session) -> int:
    """
    Fetch last 3 days of bullpen data from MLB Stats API and upsert into
    reliever_workload. Returns number of rows upserted, or 0 on failure.
    """
    try:
        three_days_ago = target_date - timedelta(days=3)
        yesterday = target_date - timedelta(days=1)
        url = (
            f"{MLB_API_BASE}/schedule"
            f"?teamId={team_id}"
            f"&startDate={three_days_ago.isoformat()}"
            f"&endDate={yesterday.isoformat()}"
            f"&hydrate=boxscore"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        upserted = 0
        dates = data.get("dates", [])
        for date_entry in dates:
            game_date_str = date_entry.get("date", "")
            try:
                game_date_obj = date.fromisoformat(game_date_str)
            except ValueError:
                continue

            for game_entry in date_entry.get("games", []):
                boxscore = game_entry.get("boxscore", {})
                # Check both home and away teams for this team_id
                for side in ("home", "away"):
                    team_data = boxscore.get("teams", {}).get(side, {})
                    if team_data.get("team", {}).get("id") != team_id:
                        continue
                    pitchers = team_data.get("pitchers", [])
                    # Skip index 0 (starter); rest are relievers
                    for player_id in pitchers[1:]:
                        player_info = team_data.get("players", {}).get(f"ID{player_id}", {})
                        stats = player_info.get("stats", {}).get("pitching", {})
                        pitches = stats.get("pitchesThrown", 0) or 0
                        player_name = player_info.get("person", {}).get("fullName", "")

                        # Count appearances in last 3 days
                        appearances = (
                            db.query(RelieverWorkload)
                            .filter(
                                RelieverWorkload.player_id == player_id,
                                RelieverWorkload.date >= three_days_ago,
                                RelieverWorkload.date <= yesterday,
                            )
                            .count()
                        )
                        # Days rest = days since last appearance
                        last_app = (
                            db.query(RelieverWorkload)
                            .filter(
                                RelieverWorkload.player_id == player_id,
                                RelieverWorkload.date < game_date_obj,
                            )
                            .order_by(RelieverWorkload.date.desc())
                            .first()
                        )
                        if last_app:
                            days_rest = (game_date_obj - last_app.date).days
                        else:
                            days_rest = 99

                        existing = (
                            db.query(RelieverWorkload)
                            .filter(
                                RelieverWorkload.player_id == player_id,
                                RelieverWorkload.date == game_date_obj,
                            )
                            .first()
                        )
                        if existing:
                            existing.pitches_thrown = pitches
                            existing.days_rest = days_rest
                            existing.appearances_last_3_days = appearances + 1
                            existing.player_name = player_name
                        else:
                            db.add(RelieverWorkload(
                                player_id=player_id,
                                team_id=team_id,
                                date=game_date_obj,
                                pitches_thrown=pitches,
                                days_rest=days_rest,
                                appearances_last_3_days=appearances + 1,
                                player_name=player_name,
                            ))
                        upserted += 1

        db.commit()
        return upserted
    except Exception as e:
        db.rollback()
        print(f"[v4 bullpen] collect_reliever_workload silenced error: {e}")
        return 0


def calculate_fatigue_score(
    player_id: int, team_id: int, target_date: date, db: Session
) -> float:
    """
    Returns a fatigue-adjusted strength score (0.0 = exhausted, 1.0 = fresh).
    Never raises.
    """
    try:
        manager = _get_manager(team_id, db)
        three_days_ago = target_date - timedelta(days=3)
        rows = (
            db.query(RelieverWorkload)
            .filter(
                RelieverWorkload.player_id == player_id,
                RelieverWorkload.date >= three_days_ago,
                RelieverWorkload.date < target_date,
            )
            .all()
        )
        if not rows:
            return 1.0  # no data → assume fresh

        pitches_3_days = sum(r.pitches_thrown or 0 for r in rows)
        appearances_3_days = len(rows)
        latest = max(rows, key=lambda r: r.date)
        days_rest = (target_date - latest.date).days

        base_fatigue = min(pitches_3_days / 150.0, 1.0)

        if manager["b2b_usage_rate"] < 0.15 and days_rest == 0:
            return 0.0

        if appearances_3_days >= 3:
            base_fatigue = min(base_fatigue * 1.5, 1.0)

        return max(0.0, min(1.0, 1.0 - base_fatigue))
    except Exception:
        return 1.0


def get_team_bullpen_availability(team_id: int, target_date: date, db: Session) -> float:
    """
    Returns aggregate Bullpen Strength Rating 0.0–1.0.
    Returns 1.0 if no data available.
    """
    try:
        three_days_ago = target_date - timedelta(days=3)
        rows = (
            db.query(RelieverWorkload)
            .filter(
                RelieverWorkload.team_id == team_id,
                RelieverWorkload.date >= three_days_ago,
                RelieverWorkload.date < target_date,
            )
            .all()
        )
        if not rows:
            print(f"[v4 bullpen] team_id={team_id} no workload data → strength=1.00")
            return 1.0

        # Get unique player IDs that appeared
        player_ids = list({r.player_id for r in rows if r.player_id})
        if not player_ids:
            return 1.0

        scores = [
            calculate_fatigue_score(pid, team_id, target_date, db)
            for pid in player_ids
        ]
        strength = sum(scores) / len(scores)
        strength = round(max(0.0, min(1.0, strength)), 4)
        print(f"[v4 bullpen] team_id={team_id} strength={strength:.2f}")
        return strength
    except Exception as e:
        print(f"[v4 bullpen] get_team_bullpen_availability silenced error: {e}")
        return 1.0
