def build_team_features(raw_stats: dict, wins: int | None = None, losses: int | None = None) -> dict:
    wins = wins or 40
    losses = losses or 40
    games_played = max(wins + losses, 1)

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
