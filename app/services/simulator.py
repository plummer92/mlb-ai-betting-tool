import random


def simulate_runs(offense_rpg: float, opponent_era: float, home_boost: float = 0.0) -> int:
    era_factor = max(0.65, min(1.35, (5.00 - opponent_era) / 2.5 + 1.0))
    mean_runs = offense_rpg * era_factor + home_boost
    noise = random.uniform(-2.0, 2.0)
    return max(0, round(mean_runs + noise))


def run_monte_carlo(
    away_team: dict,
    home_team: dict,
    sim_count: int = 1000,
    weather_modifier: float = 1.0,
) -> dict:
    away_wins = 0
    home_wins = 0
    away_scores = []
    home_scores = []

    for _ in range(sim_count):
        away_runs = simulate_runs(
            offense_rpg=away_team["runs_per_game"],
            opponent_era=home_team["era"],
            home_boost=0.0,
        ) * weather_modifier
        home_runs = simulate_runs(
            offense_rpg=home_team["runs_per_game"],
            opponent_era=away_team["era"],
            home_boost=0.25,
        ) * weather_modifier

        away_runs = max(0, round(away_runs))
        home_runs = max(0, round(home_runs))

        away_scores.append(away_runs)
        home_scores.append(home_runs)


        if away_runs > home_runs:
            away_wins += 1
        elif home_runs > away_runs:
            home_wins += 1
        else:
            away_wins += 0.5
            home_wins += 0.5

    away_win_pct = away_wins / sim_count
    home_win_pct = home_wins / sim_count
    projected_away_score = sum(away_scores) / len(away_scores)
    projected_home_score = sum(home_scores) / len(home_scores)
    projected_total = projected_away_score + projected_home_score
    confidence_score = abs(home_win_pct - away_win_pct) * 100

    recommended_side = "AWAY" if away_win_pct > home_win_pct else "HOME"

    sim_totals = [a + h for a, h in zip(away_scores, home_scores)]

    return {
        "away_win_pct": round(away_win_pct, 4),
        "home_win_pct": round(home_win_pct, 4),
        "projected_away_score": round(projected_away_score, 2),
        "projected_home_score": round(projected_home_score, 2),
        "projected_total": round(projected_total, 2),
        "confidence_score": round(confidence_score, 2),
        "recommended_side": recommended_side,
        "sim_count": sim_count,
        "sim_totals": sim_totals,
    }
