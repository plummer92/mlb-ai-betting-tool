"""
v0.5 Weather/Wind Module.

Fetches game-time weather from Open-Meteo and calculates a wind factor
that adjusts run projections based on wind speed and direction relative
to each park's center-field bearing.

Shadow-mode only — never raises, never touches v3 tables.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Optional

import requests

from app.services.travel_service import CITY_COORDS

# ---------------------------------------------------------------------------
# Compass bearing (degrees) from home plate toward center field per park.
# 0 = north CF, 90 = east CF, 180 = south CF, 270 = west CF.
# Parks with 0 and in DOME_TEAMS are ignored; non-dome parks assigned 0
# default to due north (best-effort fallback).
# ---------------------------------------------------------------------------
CF_BEARING: dict[int, int] = {
    108: 215,  # Angels Stadium
    109: 0,    # Chase Field (dome)
    110: 26,   # Camden Yards
    111: 95,   # Fenway Park
    112: 315,  # Wrigley Field (famous NW wind)
    113: 0,    # GABP
    114: 5,    # Progressive Field
    115: 292,  # Coors Field
    116: 355,  # Comerica Park
    117: 0,    # Minute Maid Park (dome)
    118: 0,    # Kauffman Stadium
    119: 50,   # Dodger Stadium
    120: 4,    # Nationals Park
    121: 55,   # Citi Field
    133: 310,  # Oakland Coliseum
    134: 335,  # PNC Park
    135: 309,  # Petco Park
    136: 0,    # T-Mobile Park (retractable)
    137: 65,   # Oracle Park
    138: 70,   # Busch Stadium
    139: 0,    # Tropicana Field (dome)
    140: 315,  # Globe Life Field
    141: 0,    # Rogers Centre (retractable)
    142: 0,    # Target Field
    143: 40,   # Citizens Bank Park
    144: 350,  # Truist Park
    145: 340,  # Guaranteed Rate Field
    146: 0,    # loanDepot Park (retractable)
    147: 25,   # Yankee Stadium
    158: 355,  # American Family Field
}

# Fixed domes and retractable roofs that neutralize wind.
DOME_TEAMS: set[int] = {109, 117, 136, 139, 141, 146}

# In-memory cache: (team_id, game_date_str) -> weather dict
_weather_cache: dict[tuple[int, str], dict] = {}

_NEUTRAL_WEATHER = {
    "temp_f": 72.0,
    "wind_mph": 0.0,
    "wind_dir_deg": 0.0,
    "humidity_pct": 50.0,
    "wind_factor": 0.0,
    "is_dome": False,
}


def calculate_wind_factor(wind_mph: float, wind_dir_deg: float, cf_bearing: int) -> float:
    """
    Return wind factor in [-1.0, +1.0].

    +1.0 = strong wind directly out to CF (hitter friendly)
    -1.0 = strong wind blowing in from CF (pitcher friendly)
    """
    angle_diff = (wind_dir_deg - cf_bearing + 180) % 360 - 180
    raw = math.cos(math.radians(angle_diff))
    wind_factor = raw * min(wind_mph / 15.0, 1.0)
    return round(wind_factor, 3)


def fetch_park_weather(team_id: int, game_date: date) -> dict:
    """
    Fetch game-time weather for the park of *team_id* on *game_date*.

    Uses Open-Meteo free API; extracts hour-19 values as a 7 PM local
    game-time estimate. Returns a neutral dict on any error or for dome parks.
    Never raises.
    """
    cache_key = (team_id, str(game_date))
    if cache_key in _weather_cache:
        return _weather_cache[cache_key]

    is_dome = team_id in DOME_TEAMS
    if is_dome:
        result = {**_NEUTRAL_WEATHER, "is_dome": True}
        _weather_cache[cache_key] = result
        return result

    coords = CITY_COORDS.get(team_id)
    if coords is None:
        _weather_cache[cache_key] = dict(_NEUTRAL_WEATHER)
        return dict(_NEUTRAL_WEATHER)

    lat, lng = coords
    date_str = str(game_date)

    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lng,
                "hourly": "temperature_2m,windspeed_10m,winddirection_10m,relativehumidity_2m",
                "temperature_unit": "fahrenheit",
                "windspeed_unit": "mph",
                "start_date": date_str,
                "end_date": date_str,
                "timezone": "America/New_York",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        hourly = data.get("hourly", {})
        # Hour 19 = 7 PM local time (index 19 in the 24-slot hourly array)
        hour_idx = 19

        temp_f = float(hourly["temperature_2m"][hour_idx])
        wind_mph = float(hourly["windspeed_10m"][hour_idx])
        wind_dir_deg = float(hourly["winddirection_10m"][hour_idx])
        humidity_pct = float(hourly["relativehumidity_2m"][hour_idx])

        cf_bearing = CF_BEARING.get(team_id, 0)
        wind_factor = calculate_wind_factor(wind_mph, wind_dir_deg, cf_bearing)

        result = {
            "temp_f": round(temp_f, 1),
            "wind_mph": round(wind_mph, 1),
            "wind_dir_deg": round(wind_dir_deg, 1),
            "humidity_pct": round(humidity_pct, 1),
            "wind_factor": wind_factor,
            "is_dome": False,
        }
        _weather_cache[cache_key] = result
        return result

    except Exception as e:
        print(f"[weather_service] fetch_park_weather({team_id}, {game_date}) non-fatal: {e}")
        result = dict(_NEUTRAL_WEATHER)
        _weather_cache[cache_key] = result
        return result
