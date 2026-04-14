"""
v0.4 Umpire data service.

All functions catch exceptions silently so v3 pipeline is never affected.
"""

from __future__ import annotations

from typing import Optional

import requests
from sqlalchemy.orm import Session

from app.models.schema import UmpireAssignmentV4

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

# In-memory cache: game_id -> umpire_name
_umpire_cache: dict[int, Optional[str]] = {}

# Seed data: (name, run_expectancy_impact, historical_k_rate_delta)
_KNOWN_UMPIRES: list[tuple[str, float, float]] = [
    # Run suppressors
    ("Dan Iassogna",      -0.45,  0.08),
    ("Mark Wegner",       -0.38,  0.06),
    ("Mike Everitt",      -0.31,  0.05),
    ("Laz Diaz",          -0.28,  0.09),
    ("Doug Eddings",      -0.25,  0.07),
    # Hitter-friendly
    ("Angel Hernandez",    0.52, -0.08),
    ("CB Bucknor",         0.48, -0.09),
    ("Joe West",           0.41, -0.06),
    ("Phil Cuzzi",         0.35, -0.05),
    ("Alfonso Marquez",    0.28, -0.04),
    # Neutral
    ("Bill Miller",       -0.08,  0.01),
    ("Hunter Wendelstedt", 0.12, -0.02),
]


def seed_known_umpires(db: Session) -> None:
    """
    Seed umpire_assignments_v4 with known historical umpire profiles.
    Uses NULL game_id, season=2025 for seed rows. Never duplicates by name.
    """
    existing_names = {
        row.umpire_name
        for row in db.query(UmpireAssignmentV4.umpire_name)
        .filter(UmpireAssignmentV4.game_id.is_(None))
        .all()
    }
    seeded = 0
    pending_rows: list[UmpireAssignmentV4] = []
    try:
        for name, run_impact, k_delta in _KNOWN_UMPIRES:
            if name in existing_names:
                continue
            pending_rows.append(UmpireAssignmentV4(
                game_id=None,
                umpire_name=name,
                run_expectancy_impact=run_impact,
                historical_k_rate_delta=k_delta,
                season=2025,
            ))
            seeded += 1
        if pending_rows:
            db.add_all(pending_rows)
    except Exception as e:
        db.rollback()
        print(f"[v4 umpire] seed_known_umpires insert error: {e}", flush=True)
        return

    db.commit()
    count = db.query(UmpireAssignmentV4).count()
    print(f"[v4 umpire] Seeded {seeded} umpires", flush=True)
    print(f"[v4 umpire] rows after seed: {count}", flush=True)


def fetch_umpire_assignment(game_id: int) -> Optional[str]:
    """
    Fetch HP umpire name from MLB Stats API boxscore.
    Caches result in memory. Returns None on failure.
    """
    if game_id in _umpire_cache:
        return _umpire_cache[game_id]
    try:
        url = f"{MLB_API_BASE}/game/{game_id}/boxscore"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        officials = data.get("officials", [])
        hp_umpire = None
        for official in officials:
            if official.get("officialType") == "Home Plate":
                hp_umpire = official.get("official", {}).get("fullName")
                break
        _umpire_cache[game_id] = hp_umpire
        return hp_umpire
    except Exception as e:
        print(f"[v4 umpire] fetch_umpire_assignment({game_id}) silenced error: {e}")
        _umpire_cache[game_id] = None
        return None


def get_umpire_run_impact(umpire_name: str, db: Session) -> float:
    """
    Query historical average run_expectancy_impact for a given umpire.
    Returns 0.0 if not found.
    """
    try:
        rows = (
            db.query(UmpireAssignmentV4)
            .filter(UmpireAssignmentV4.umpire_name == umpire_name)
            .all()
        )
        if not rows:
            return 0.0
        impacts = [r.run_expectancy_impact for r in rows if r.run_expectancy_impact is not None]
        if not impacts:
            return 0.0
        return sum(impacts) / len(impacts)
    except Exception:
        return 0.0


def collect_umpire_for_game(game_id: int, season: int, db: Session) -> Optional[dict]:
    """
    Fetch umpire name, look up run impact, upsert into umpire_assignments_v4.
    Returns result dict or None on failure.
    """
    try:
        umpire_name = fetch_umpire_assignment(game_id)
        if not umpire_name:
            umpire_name = "Unknown"
            run_impact = 0.0
        else:
            run_impact = get_umpire_run_impact(umpire_name, db)

        # Check if we already have a row for this game
        existing = (
            db.query(UmpireAssignmentV4)
            .filter(
                UmpireAssignmentV4.game_id == game_id,
            )
            .first()
        )
        if existing:
            existing.umpire_name = umpire_name
            existing.run_expectancy_impact = run_impact
            existing.historical_k_rate_delta = existing.historical_k_rate_delta or 0.0
            existing.season = season
        else:
            db.add(UmpireAssignmentV4(
                game_id=game_id,
                umpire_name=umpire_name,
                run_expectancy_impact=run_impact,
                historical_k_rate_delta=0.0,
                season=season,
            ))
        db.commit()
        return {
            "umpire_name": umpire_name,
            "run_impact": run_impact,
        }
    except Exception as e:
        db.rollback()
        print(f"[v4 umpire] collect_umpire_for_game({game_id}) silenced error: {e}")
        return None
