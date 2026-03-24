def build_team_features(raw_stats: dict, wins: int | None = None, losses: int | None = None) -> dict:
    wins = wins or 0
    losses = losses or 0
    # When the season hasn't started yet (0-0), use full-season denominator so
    # runs_per_game is calculated correctly against prior-year stat totals.
    games_played = wins + losses if (wins + losses) > 0 else 162

    runs_per_game = raw_stats["runs"] / games_played

    return {
        "era": raw_stats["era"],
        "whip": raw_stats["whip"],
        "avg": raw_stats["avg"],
        "ops": raw_stats["ops"],
        "home_runs": raw_stats["home_runs"],
        "runs_per_game": runs_per_game,
        "win_pct": wins / games_played,
    }


_DOME_VENUES = [
    "Tropicana",
    "Rogers Centre",
    "Minute Maid",
    "T-Mobile",
    "loanDepot",
    "Chase Field",
    "American Family",
    "Globe Life",
]


def is_dome_venue(venue_name: str | None) -> bool:
    if not venue_name:
        return False
    lower = venue_name.lower()
    return any(d.lower() in lower for d in _DOME_VENUES)


def weather_run_modifier(
    temp: int | None,
    wind_mph: int | None,
    wind_dir: str | None,
    is_dome: bool = False,
) -> float:
    """
    Returns a run-scoring multiplier based on weather conditions.
    1.0 = neutral. >1.0 = more runs expected. <1.0 = fewer.

    Temperature: ~0.4% per degree above/below 72°F baseline.
    Wind out >8mph: HR boost. Wind in >8mph: HR suppression.
    Dome parks ignore all weather effects.
    """
    if is_dome:
        return 1.0

    modifier = 1.0

    if temp is not None:
        modifier += (temp - 72) * 0.004

    if wind_mph and wind_dir:
        direction = wind_dir.lower()
        if "out" in direction:
            if wind_mph > 15:
                modifier += 0.08
            elif wind_mph > 8:
                modifier += 0.04
        elif "in" in direction:
            if wind_mph > 15:
                modifier -= 0.07
            elif wind_mph > 8:
                modifier -= 0.03

    return round(max(0.75, min(1.25, modifier)), 4)
