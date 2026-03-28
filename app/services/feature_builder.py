# Park factors derived from backtest correlation analysis (vs_baseline home win rate).
# Positive = venue boosts home win probability; negative = suppresses it.
# All unlisted venues default to 0.0 (league-average neutral).
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


def build_team_features(
    raw_stats: dict,
    wins: int | None = None,
    losses: int | None = None,
    starter_stats: dict | None = None,
    venue: str | None = None,
    bullpen_stats: dict | None = None,
) -> dict:
    # Prefer games_played from the stats API; fall back to wins+losses or season default
    games_played = raw_stats.get("games_played") or 0
    if games_played == 0:
        wins = wins or 40
        losses = losses or 40
        games_played = wins + losses
    games_played = max(games_played, 1)

    runs_per_game = raw_stats["runs"] / games_played

    # run_differential_per_game replaces win_pct (backtest: zero predictive power)
    runs_allowed = raw_stats.get("runs_allowed")
    run_differential_per_game = (
        (raw_stats["runs"] - runs_allowed) / games_played
        if runs_allowed is not None
        else None
    )

    # Use starter ERA/WHIP when available; fall back to team totals.
    # Prefer xERA over ERA when the Statcast fetch succeeded (include_xera=True
    # path in fetch_pitcher_stats); xERA strips out luck/BABIP noise.
    if starter_stats:
        era  = starter_stats.get("xera") or starter_stats["era"]
        whip = starter_stats["whip"]
        using_xera = bool(starter_stats.get("xera"))
    else:
        era        = raw_stats["era"]
        whip       = raw_stats["whip"]
        using_xera = False

    return {
        "era": era,
        "whip": whip,
        "using_xera": using_xera,
        "starter_k9":  starter_stats["k9"]  if starter_stats else None,
        "starter_bb9": starter_stats["bb9"] if starter_stats else None,
        "avg": raw_stats["avg"],
        "ops": raw_stats["ops"],
        "home_runs": raw_stats["home_runs"],
        "runs_per_game": runs_per_game,
        "run_differential_per_game": run_differential_per_game,
        # Park factor: only meaningful when this dict is built for the home team.
        # Passed to run_monte_carlo where it adjusts the home-field advantage.
        "park_factor": PARK_FACTORS.get(venue, 0.0) if venue else 0.0,
        # Bullpen ERA: from reliever split when available; falls back to team ERA.
        "bullpen_era": bullpen_stats["era"] if bullpen_stats else raw_stats["era"],
    }
