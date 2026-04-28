"""
v0.5 Travel Stress Module.

Estimates the fatigue burden a team carries from recent travel.
Score range: 0.0 (no stress) → 1.0 (maximum stress).

Shadow-mode only — never raises, never touches v3 tables.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.schema import Game

# ---------------------------------------------------------------------------
# Team home-city data keyed by MLB Stats API team_id
# lat/lng = home stadium coordinates
# utc_offset_dst = hours offset from UTC during DST (MLB season Apr–Oct)
# ---------------------------------------------------------------------------
CITY_COORDS: dict[int, tuple[float, float]] = {
    108: (33.8003, -117.8827),   # Angels       – Anaheim, CA
    109: (33.4453, -112.0667),   # Diamondbacks – Phoenix, AZ
    110: (39.2838, -76.6219),    # Orioles       – Baltimore, MD
    111: (42.3467, -71.0972),    # Red Sox       – Boston, MA
    112: (41.9484, -87.6553),    # Cubs          – Chicago, IL
    113: (39.0975, -84.5065),    # Reds          – Cincinnati, OH
    114: (41.4958, -81.6852),    # Guardians     – Cleveland, OH
    115: (39.7559, -104.9942),   # Rockies       – Denver, CO
    116: (42.3390, -83.0485),    # Tigers        – Detroit, MI
    117: (29.7573, -95.3555),    # Astros        – Houston, TX
    118: (39.0517, -94.4803),    # Royals        – Kansas City, MO
    119: (34.0739, -118.2400),   # Dodgers       – Los Angeles, CA
    120: (38.8730, -77.0074),    # Nationals     – Washington, DC
    121: (40.7571, -73.8458),    # Mets          – New York, NY
    133: (37.7516, -122.2005),   # Athletics     – Oakland, CA
    134: (40.4469, -80.0057),    # Pirates       – Pittsburgh, PA
    135: (32.7073, -117.1566),   # Padres        – San Diego, CA
    136: (47.5914, -122.3325),   # Mariners      – Seattle, WA
    137: (37.7786, -122.3893),   # Giants        – San Francisco, CA
    138: (38.6226, -90.1928),    # Cardinals     – St. Louis, MO
    139: (27.7683, -82.6534),    # Rays          – St. Petersburg, FL
    140: (32.7512, -97.0832),    # Rangers       – Arlington, TX
    141: (43.6414, -79.3894),    # Blue Jays     – Toronto, ON (treated as EDT)
    142: (44.9817, -93.2781),    # Twins         – Minneapolis, MN
    143: (39.9057, -75.1665),    # Phillies      – Philadelphia, PA
    144: (33.8907, -84.4677),    # Braves        – Atlanta, GA
    145: (41.8299, -87.6338),    # White Sox     – Chicago, IL
    146: (25.7781, -80.2197),    # Marlins       – Miami, FL
    147: (40.8296, -73.9262),    # Yankees       – New York, NY
    158: (43.0280, -87.9712),    # Brewers       – Milwaukee, WI
}

# UTC offset (hours) during DST (MLB season).
# EDT=-4, CDT=-5, MDT=-6, PDT=-7, MST=-7 (AZ no DST)
TIMEZONE_MAP: dict[int, int] = {
    108: -7,   # Angels       – PDT
    109: -7,   # Diamondbacks – MST (Arizona skips DST)
    110: -4,   # Orioles       – EDT
    111: -4,   # Red Sox       – EDT
    112: -5,   # Cubs          – CDT
    113: -4,   # Reds          – EDT
    114: -4,   # Guardians     – EDT
    115: -6,   # Rockies       – MDT
    116: -4,   # Tigers        – EDT
    117: -5,   # Astros        – CDT
    118: -5,   # Royals        – CDT
    119: -7,   # Dodgers       – PDT
    120: -4,   # Nationals     – EDT
    121: -4,   # Mets          – EDT
    133: -7,   # Athletics     – PDT
    134: -4,   # Pirates       – EDT
    135: -7,   # Padres        – PDT
    136: -7,   # Mariners      – PDT
    137: -7,   # Giants        – PDT
    138: -5,   # Cardinals     – CDT
    139: -4,   # Rays          – EDT
    140: -5,   # Rangers       – CDT
    141: -4,   # Blue Jays     – EDT
    142: -5,   # Twins         – CDT
    143: -4,   # Phillies      – EDT
    144: -4,   # Braves        – EDT
    145: -5,   # White Sox     – CDT
    146: -4,   # Marlins       – EDT
    147: -4,   # Yankees       – EDT
    158: -5,   # Brewers       – CDT
}


def _is_day_game(start_time_str: Optional[str], home_team_id: Optional[int]) -> bool:
    """
    Return True if the game starts before 17:00 local time at the home park.
    Falls back to False on any parse error.
    """
    if not start_time_str or not home_team_id:
        return False
    try:
        # start_time is stored as an ISO string (may end in Z or have offset)
        ts = start_time_str.replace("Z", "+00:00")
        utc_dt = datetime.fromisoformat(ts)
        utc_offset = TIMEZONE_MAP.get(home_team_id, -5)
        local_hour = (utc_dt.hour + utc_offset) % 24
        return local_hour < 17
    except Exception:
        return False


def calculate_travel_stress(
    team_id: int,
    game_date: date,
    db: Session,
) -> float:
    """
    Return a travel-stress score in [0.0, 1.0] for *team_id* on *game_date*.

    Scoring factors
    ---------------
    - Timezone crossings (west-to-east body-clock disruption weighs more)
    - Days rest since last game (back-to-back travel is hardest)
    - Day game immediately after cross-timezone travel

    Returns 0.0 on any error or missing data.
    """
    try:
        # ── 1. Find the current game for this team ────────────────────────
        current_game: Optional[Game] = (
            db.query(Game)
            .filter(
                or_(
                    Game.home_team_id == team_id,
                    Game.away_team_id == team_id,
                ),
                Game.game_date == game_date,
            )
            .first()
        )
        if current_game is None:
            return 0.0

        # ── 2. Find the most recent prior game ────────────────────────────
        prev_game: Optional[Game] = (
            db.query(Game)
            .filter(
                or_(
                    Game.home_team_id == team_id,
                    Game.away_team_id == team_id,
                ),
                Game.game_date < game_date,
            )
            .order_by(Game.game_date.desc())
            .first()
        )
        if prev_game is None:
            return 0.0

        # ── 3. Resolve the city (team_id) for each game ──────────────────
        # The "city" of a game is the home team's city.
        prev_city_team_id: Optional[int] = prev_game.home_team_id
        curr_city_team_id: Optional[int] = current_game.home_team_id

        if not prev_city_team_id or not curr_city_team_id:
            return 0.0

        # ── 4. Same city → no travel ──────────────────────────────────────
        if prev_city_team_id == curr_city_team_id:
            return 0.0

        # ── 5. Days rest ──────────────────────────────────────────────────
        days_rest = (game_date - prev_game.game_date).days  # 1 = back-to-back

        # ── 6. Timezone crossing ──────────────────────────────────────────
        prev_tz = TIMEZONE_MAP.get(prev_city_team_id, -5)
        curr_tz = TIMEZONE_MAP.get(curr_city_team_id, -5)
        # Positive tz_delta = team moved east (west-to-east, harder)
        # Negative tz_delta = team moved west (east-to-west, easier)
        tz_delta = curr_tz - prev_tz  # e.g. PDT→EDT: -4 - (-7) = +3

        # ── 7. Day game after travel ──────────────────────────────────────
        day_game = _is_day_game(current_game.start_time, curr_city_team_id)

        # ── 8. Build stress score ─────────────────────────────────────────
        stress = 0.0

        # Days-rest component (only relevant when travel actually occurred)
        if days_rest == 1:
            stress += 0.30   # back-to-back travel
        elif days_rest == 2:
            stress += 0.10   # one day to recover
        # days_rest >= 3 → negligible rest penalty

        # Timezone component
        tz_crossed = abs(tz_delta)
        if tz_crossed >= 3:
            # Cross-country (e.g. LAD → NYY or vice-versa)
            stress += 0.40 if tz_delta > 0 else 0.25   # eastward harder
        elif tz_crossed == 2:
            stress += 0.25 if tz_delta > 0 else 0.15
        elif tz_crossed == 1:
            stress += 0.10

        # Day-game-after-travel kicker
        if day_game and days_rest <= 1 and tz_crossed >= 2:
            stress += 0.20

        return min(1.0, round(stress, 4))

    except Exception as e:
        print(f"[travel_service] calculate_travel_stress({team_id}, {game_date}) non-fatal: {e}")
        return 0.0
