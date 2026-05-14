# Park factors derived from backtest correlation analysis (vs_baseline home win rate).
# They are intentionally shrunk and used only as a modest run-environment input.
PARK_FACTORS: dict[str, float] = {
    "Dodger Stadium":        +0.144,
    "Truist Park":           +0.105,
    "Citizens Bank Park":    +0.095,
    "Tropicana Field":       +0.076,
    "Yankee Stadium":        +0.063,
    "Minute Maid Park":      +0.051,
    "T-Mobile Park":         +0.051,
    "Nationals Park":        -0.124,
    "Oakland Coliseum":      -0.141,
    "Guaranteed Rate Field": -0.151,
}

PYTHAG_EXPONENT = 1.83
LEAGUE_AVG_RUNS_PER_GAME = 4.4


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def build_team_features(
    raw_stats: dict,
    wins: int | None = None,
    losses: int | None = None,
    starter_stats: dict | None = None,
    venue: str | None = None,
    bullpen_stats: dict | None = None,
    statcast_team: dict | None = None,
    travel_stress: dict | None = None,
    series_position: dict | None = None,
) -> dict:
    # Prefer games_played from the stats API; fall back to wins+losses or season default
    games_played = raw_stats.get("games_played") or 0
    if games_played == 0:
        wins = wins or 40
        losses = losses or 40
        games_played = wins + losses
    games_played = max(games_played, 1)

    runs_per_game = raw_stats["runs"] / games_played

    runs_allowed = raw_stats.get("runs_allowed")
    run_differential_per_game = (
        (raw_stats["runs"] - runs_allowed) / games_played
        if runs_allowed is not None
        else None
    )
    pythagorean_win_pct = None
    if runs_allowed is not None:
        scored = max(float(raw_stats["runs"]), 1.0)
        allowed = max(float(runs_allowed), 1.0)
        pythagorean_win_pct = round(
            (scored ** PYTHAG_EXPONENT) / ((scored ** PYTHAG_EXPONENT) + (allowed ** PYTHAG_EXPONENT)),
            4,
        )

    starter_run_prevention = None
    starter_whip = raw_stats["whip"]
    starter_kbb = None
    starter_kbb_percent = None
    using_xera = False
    if starter_stats:
        starter_xera = starter_stats.get("xera")
        starter_era = starter_stats.get("era")
        starter_run_prevention = starter_xera if starter_xera is not None else starter_era
        starter_whip = starter_stats.get("whip", raw_stats["whip"])
        starter_kbb = starter_stats.get("kbb")
        starter_kbb_percent = starter_stats.get("kbb_percent")
        if starter_kbb_percent is None and starter_kbb is not None:
            starter_kbb_percent = round(_clamp(float(starter_kbb) / 45.0, 0.05, 0.25), 4)
        using_xera = starter_xera is not None

    park_delta = PARK_FACTORS.get(venue, 0.0) if venue else 0.0
    park_run_factor = round(_clamp(1.0 + (park_delta * 0.12), 0.96, 1.04), 4)

    return {
        "using_xera": using_xera,
        "starter_xera": starter_stats.get("xera") if starter_stats else None,
        "starter_run_prevention": starter_run_prevention,
        "starter_whip": starter_whip,
        "starter_kbb": starter_kbb,
        "starter_kbb_percent": starter_kbb_percent,
        "team_whip": raw_stats["whip"],
        "avg": raw_stats["avg"],
        "ops": raw_stats["ops"],
        "home_runs": raw_stats["home_runs"],
        "runs_per_game": runs_per_game,
        "run_differential_per_game": run_differential_per_game,
        "pythagorean_win_pct": pythagorean_win_pct,
        "park_run_factor": park_run_factor,
        "bullpen_run_prevention": bullpen_stats["era"] if bullpen_stats else None,
        "exit_velocity_avg": statcast_team.get("exit_velocity_avg") if statcast_team else None,
        "barrel_rate":       statcast_team.get("barrel_rate")       if statcast_team else None,
        "hard_hit_rate":     statcast_team.get("hard_hit_rate")     if statcast_team else None,
        "sprint_speed_avg":  statcast_team.get("sprint_speed_avg")  if statcast_team else None,
        "away_travel_stress": travel_stress.get("stress_score", 0.0) if travel_stress else 0.0,
        "home_travel_stress": 0.0,
        "series_game_number": series_position.get("series_game_number", 1) if series_position else 1,
        "is_series_opener": series_position.get("is_series_opener", False) if series_position else False,
    }
