def build_team_features(
    raw_stats: dict,
    wins: int | None = None,
    losses: int | None = None,
    starter_stats: dict | None = None,
) -> dict:
    wins = wins or 40
    losses = losses or 40
    games_played = max(wins + losses, 1)

    runs_per_game = raw_stats["runs"] / games_played

    # Use starter ERA/WHIP when available; fall back to team totals
    era  = starter_stats["era"]  if starter_stats else raw_stats["era"]
    whip = starter_stats["whip"] if starter_stats else raw_stats["whip"]

    return {
        "era": era,
        "whip": whip,
        "starter_k9":  starter_stats["k9"]  if starter_stats else None,
        "starter_bb9": starter_stats["bb9"] if starter_stats else None,
        "avg": raw_stats["avg"],
        "ops": raw_stats["ops"],
        "home_runs": raw_stats["home_runs"],
        "runs_per_game": runs_per_game,
        "win_pct": wins / games_played,
    }
