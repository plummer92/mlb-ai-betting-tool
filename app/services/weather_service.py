"""
Open-Meteo weather forecasts for MLB ballparks.
Free, no API key required. Used for morning odds snapshots when MLB's own
weather data isn't populated yet (typically only available near game time).
"""

from datetime import datetime

import httpx

# Park coordinates (lat, lon) for all 30 MLB venues.
# Key matches the venue name stored from MLB Stats API.
PARK_COORDS: dict[str, tuple[float, float]] = {
    "Yankee Stadium":              (40.8296, -73.9262),
    "Fenway Park":                 (42.3467, -71.0972),
    "Wrigley Field":               (41.9484, -87.6553),
    "Guaranteed Rate Field":       (41.8299, -87.6338),
    "Comerica Park":               (42.3390, -83.0485),
    "Progressive Field":           (41.4962, -81.6852),
    "Kauffman Stadium":            (39.0517, -94.4803),
    "Target Field":                (44.9817, -93.2781),
    "Minute Maid Park":            (29.7573, -95.3555),
    "Globe Life Field":            (32.7473, -97.0845),
    "Angel Stadium":               (33.8003, -117.8827),
    "Oakland Coliseum":            (37.7516, -122.2005),
    "T-Mobile Park":               (47.5914, -122.3325),
    "Tropicana Field":             (27.7683, -82.6534),
    "Rogers Centre":               (43.6414, -79.3894),
    "Oriole Park at Camden Yards":  (39.2838, -76.6218),
    "Nationals Park":              (38.8730, -77.0074),
    "Citizens Bank Park":          (39.9061, -75.1665),
    "Citi Field":                  (40.7571, -73.8458),
    "Truist Park":                 (33.8908, -84.4677),
    "loanDepot park":              (25.7781, -80.2197),
    "Great American Ball Park":    (39.0975, -84.5061),
    "American Family Field":       (43.0280, -87.9712),
    "Busch Stadium":               (38.6226, -90.1928),
    "PNC Park":                    (40.4469, -80.0057),
    "Wrigley Field":               (41.9484, -87.6553),
    "Chase Field":                 (33.4455, -112.0667),
    "Coors Field":                 (39.7559, -104.9942),
    "Dodger Stadium":              (34.0739, -118.2400),
    "Oracle Park":                 (37.7786, -122.3893),
    "Petco Park":                  (32.7076, -117.1570),
}


async def fetch_game_forecast(venue_name: str, game_time: datetime) -> dict | None:
    """
    Fetch hourly forecast from Open-Meteo for a venue at game time.
    Returns {"temp": int, "wind_mph": int, "wind_direction_deg": int, "weather_code": int}
    or None if the venue isn't in PARK_COORDS or the request fails.
    """
    coords = PARK_COORDS.get(venue_name)
    if not coords:
        return None

    lat, lon = coords
    date_str = game_time.strftime("%Y-%m-%d")

    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&hourly=temperature_2m,windspeed_10m,winddirection_10m,weathercode"
        f"&temperature_unit=fahrenheit"
        f"&windspeed_unit=mph"
        f"&start_date={date_str}&end_date={date_str}"
        f"&timezone=America%2FNew_York"
    )

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        print(f"[weather_service] Open-Meteo fetch failed for {venue_name}: {exc}")
        return None

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        return None

    target_hour = game_time.hour
    closest_idx = min(
        range(len(times)),
        key=lambda i: abs(int(times[i].split("T")[1][:2]) - target_hour),
    )

    return {
        "temp":               round(hourly["temperature_2m"][closest_idx]),
        "wind_mph":           round(hourly["windspeed_10m"][closest_idx]),
        "wind_direction_deg": hourly["winddirection_10m"][closest_idx],
        "weather_code":       hourly["weathercode"][closest_idx],
    }


def wind_deg_to_park_direction(degrees: int, venue_name: str) -> str | None:
    """
    Convert compass degrees to a park-relative wind direction string
    compatible with weather_run_modifier ("Out To CF", "In From CF", etc.).

    Park orientations (CF compass bearing from home plate).
    A wind blowing FROM that direction = "In From CF".
    A wind blowing TOWARD that direction = "Out To CF".
    """
    CF_BEARINGS: dict[str, int] = {
        "Yankee Stadium": 315,
        "Fenway Park": 90,
        "Wrigley Field": 90,
        "Coors Field": 292,
        "Oracle Park": 22,
        "Dodger Stadium": 315,
        "Citi Field": 5,
        "Citizens Bank Park": 352,
        "PNC Park": 335,
        "Busch Stadium": 345,
        "Great American Ball Park": 352,
        "Truist Park": 25,
        "Nationals Park": 8,
        "Petco Park": 315,
        "Chase Field": 340,
        "Minute Maid Park": 0,
        "Globe Life Field": 348,
        "Kauffman Stadium": 0,
        "Target Field": 340,
        "Progressive Field": 330,
        "Comerica Park": 330,
        "Guaranteed Rate Field": 340,
        "Angel Stadium": 315,
        "T-Mobile Park": 340,
        "Rogers Centre": 5,
        "Oriole Park at Camden Yards": 345,
        "American Family Field": 5,
    }

    cf_bearing = CF_BEARINGS.get(venue_name)
    if cf_bearing is None:
        return None

    # Angle between wind direction and CF bearing
    diff = (degrees - cf_bearing + 360) % 360

    # Wind blowing "out" = wind FROM behind home plate (toward CF)
    # Wind from degrees ≈ opposite of where it's blowing
    # If wind direction (where it goes) is toward CF: diff near 0
    if diff <= 45 or diff >= 315:
        return "Out To CF"
    elif 135 <= diff <= 225:
        return "In From CF"
    elif 45 < diff < 135:
        return "R To L"
    else:
        return "L To R"
