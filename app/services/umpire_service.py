"""
v0.4 Umpire data service.

All functions catch exceptions silently so v3 pipeline is never affected.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import requests
from sqlalchemy.orm import Session

from app.models.schema import Game, UmpireAssignmentV4
from app.services.travel_service import CITY_COORDS, TIMEZONE_MAP

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

# In-memory cache: game_id -> published officials. Empty/missing crews are not cached.
_umpire_cache: dict[int, list[dict[str, str]]] = {}

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


def fetch_game_officials(game_id: int) -> list[dict[str, str]]:
    """
    Fetch public game officials from MLB Stats API boxscore.
    Caches only published crew data. Returns [] on failure or pre-release.
    """
    if game_id in _umpire_cache:
        return _umpire_cache[game_id]
    try:
        url = f"{MLB_API_BASE}/game/{game_id}/boxscore"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        raw_officials = data.get("officials", [])
        officials: list[dict[str, str]] = []
        for official in raw_officials:
            name = official.get("official", {}).get("fullName")
            role = official.get("officialType")
            if name and role:
                officials.append({"umpire_name": name, "official_type": role})
        if officials:
            _umpire_cache[game_id] = officials
        return officials
    except Exception as e:
        print(f"[v4 umpire] fetch_game_officials({game_id}) silenced error: {e}")
        return []


def fetch_umpire_assignment(game_id: int) -> Optional[str]:
    """
    Fetch HP umpire name from MLB Stats API boxscore.
    Compatibility wrapper for older callers/tests.
    """
    for official in fetch_game_officials(game_id):
        if official.get("official_type") == "Home Plate":
            return official.get("umpire_name")
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


def _distance_miles(from_team_id: Optional[int], to_team_id: Optional[int]) -> Optional[float]:
    if not from_team_id or not to_team_id:
        return None
    if from_team_id == to_team_id:
        return 0.0
    start = CITY_COORDS.get(from_team_id)
    end = CITY_COORDS.get(to_team_id)
    if not start or not end:
        return None

    lat1, lon1 = map(math.radians, start)
    lat2, lon2 = map(math.radians, end)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return round(3958.8 * 2 * math.asin(math.sqrt(a)), 1)


def _previous_assignment(
    db: Session,
    umpire_name: str,
    game: Game,
) -> Optional[UmpireAssignmentV4]:
    if not game.game_date:
        return None
    return (
        db.query(UmpireAssignmentV4)
        .filter(
            UmpireAssignmentV4.umpire_name == umpire_name,
            UmpireAssignmentV4.game_id != game.game_id,
            UmpireAssignmentV4.game_date.isnot(None),
            UmpireAssignmentV4.game_date < game.game_date,
        )
        .order_by(UmpireAssignmentV4.game_date.desc(), UmpireAssignmentV4.id.desc())
        .first()
    )


def build_umpire_travel_context(
    db: Session,
    umpire_name: str,
    game: Optional[Game],
) -> dict[str, Any]:
    """
    Shadow-only travel context for officials. It is descriptive, not a model input.
    """
    if not game or not game.game_date:
        return {
            "travel_miles": None,
            "rest_days": None,
            "timezone_shift": None,
            "travel_stress": 0.0,
        }

    prev = _previous_assignment(db, umpire_name, game)
    if not prev or not prev.home_team_id:
        return {
            "travel_miles": None,
            "rest_days": None,
            "timezone_shift": None,
            "travel_stress": 0.0,
        }

    rest_days = (game.game_date - prev.game_date).days if prev.game_date else None
    travel_miles = _distance_miles(prev.home_team_id, game.home_team_id)
    prev_tz = TIMEZONE_MAP.get(prev.home_team_id)
    curr_tz = TIMEZONE_MAP.get(game.home_team_id)
    timezone_shift = (curr_tz - prev_tz) if prev_tz is not None and curr_tz is not None else None

    stress = 0.0
    if travel_miles is not None:
        stress += min(0.45, travel_miles / 3500)
    if rest_days is not None:
        if rest_days <= 1:
            stress += 0.30
        elif rest_days == 2:
            stress += 0.12
    if timezone_shift is not None:
        stress += min(0.25, abs(timezone_shift) * 0.08)

    return {
        "travel_miles": travel_miles,
        "rest_days": rest_days,
        "timezone_shift": timezone_shift,
        "travel_stress": round(min(1.0, stress), 4),
    }


def collect_umpire_for_game(game_id: int, season: int, db: Session) -> Optional[dict]:
    """
    Fetch public officials, look up HP run impact, upsert into umpire_assignments_v4.
    Returns result dict or None on failure.
    """
    try:
        game = db.query(Game).filter(Game.game_id == game_id).first()
        officials = fetch_game_officials(game_id)

        if not officials:
            officials = [{"umpire_name": "Unknown", "official_type": "Home Plate"}]

        saved_officials: list[dict[str, Any]] = []
        hp_result: Optional[dict[str, Any]] = None

        for official in officials:
            umpire_name = official["umpire_name"]
            official_type = official.get("official_type") or "Unknown"
            run_impact = (
                get_umpire_run_impact(umpire_name, db)
                if umpire_name != "Unknown" and official_type == "Home Plate"
                else 0.0
            )
            travel = build_umpire_travel_context(db, umpire_name, game) if umpire_name != "Unknown" else {
                "travel_miles": None,
                "rest_days": None,
                "timezone_shift": None,
                "travel_stress": 0.0,
            }

            existing = (
                db.query(UmpireAssignmentV4)
                .filter(
                    UmpireAssignmentV4.game_id == game_id,
                    UmpireAssignmentV4.official_type == official_type,
                )
                .first()
            )
            if not existing and official_type == "Home Plate":
                existing = (
                    db.query(UmpireAssignmentV4)
                    .filter(UmpireAssignmentV4.game_id == game_id)
                    .first()
                )

            if existing and umpire_name == "Unknown" and existing.umpire_name and existing.umpire_name != "Unknown":
                if official_type == "Home Plate":
                    hp_result = {
                        "umpire_name": existing.umpire_name,
                        "run_impact": existing.run_expectancy_impact or 0.0,
                        "officials": saved_officials,
                        "travel_context": {
                            "travel_miles": existing.travel_miles,
                            "rest_days": existing.rest_days,
                            "timezone_shift": existing.timezone_shift,
                            "travel_stress": existing.travel_stress or 0.0,
                        },
                    }
                continue

            if existing:
                existing.umpire_name = umpire_name
                existing.official_type = official_type
                existing.run_expectancy_impact = run_impact
                existing.historical_k_rate_delta = existing.historical_k_rate_delta or 0.0
                existing.season = season
                existing.venue = game.venue if game else None
                existing.home_team_id = game.home_team_id if game else None
                existing.game_date = game.game_date if game else None
                existing.travel_miles = travel["travel_miles"]
                existing.rest_days = travel["rest_days"]
                existing.timezone_shift = travel["timezone_shift"]
                existing.travel_stress = travel["travel_stress"]
            else:
                db.add(UmpireAssignmentV4(
                    game_id=game_id,
                    umpire_name=umpire_name,
                    official_type=official_type,
                    run_expectancy_impact=run_impact,
                    historical_k_rate_delta=0.0,
                    season=season,
                    venue=game.venue if game else None,
                    home_team_id=game.home_team_id if game else None,
                    game_date=game.game_date if game else None,
                    travel_miles=travel["travel_miles"],
                    rest_days=travel["rest_days"],
                    timezone_shift=travel["timezone_shift"],
                    travel_stress=travel["travel_stress"],
                ))

            item = {
                "umpire_name": umpire_name,
                "official_type": official_type,
                "run_impact": run_impact,
                "travel_context": travel,
            }
            saved_officials.append(item)
            if official_type == "Home Plate":
                hp_result = {
                    "umpire_name": umpire_name,
                    "run_impact": run_impact,
                    "officials": saved_officials,
                    "travel_context": travel,
                }

        db.commit()
        if hp_result:
            hp_result["officials"] = saved_officials
            return hp_result

        if saved_officials:
            first = saved_officials[0]
            return {
                "umpire_name": first["umpire_name"],
                "run_impact": 0.0,
                "officials": saved_officials,
                "travel_context": first["travel_context"],
            }

        return None
    except Exception as e:
        db.rollback()
        print(f"[v4 umpire] collect_umpire_for_game({game_id}) silenced error: {e}")
        return None
