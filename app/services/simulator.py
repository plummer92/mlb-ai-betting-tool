import random

# Backtest-validated feature weights (logistic regression coefficients, sprint 3)
# These can be updated at runtime via set_weights() when a new backtest runs.
_ERA_W  = 0.42
_WHIP_W = 0.36
_OPS_W  = 0.15
_TOTAL_W = _ERA_W + _WHIP_W + _OPS_W  # 0.93


def set_weights(era_w: float, whip_w: float, ops_w: float) -> None:
    """Update simulator feature weights from backtest regression coefficients."""
    global _ERA_W, _WHIP_W, _OPS_W, _TOTAL_W
    _ERA_W   = era_w
    _WHIP_W  = whip_w
    _OPS_W   = ops_w
    _TOTAL_W = _ERA_W + _WHIP_W + _OPS_W

# Explicit home field advantage in probability points (tunable)
HOME_FIELD_ADVANTAGE = 0.04

MODEL_VERSION = "v0.2-backtest-weighted"


def simulate_runs(offense: dict, opponent: dict) -> int:
    # ERA factor: higher opponent ERA = more runs for offense
    era_factor = max(0.65, min(1.35, (opponent["era"] - 5.00) / 2.5 + 1.0))

    # WHIP factor: higher opponent WHIP = more runs for offense
    whip_factor = max(0.65, min(1.35, (opponent["whip"] - 1.30) / 0.40 + 1.0))

    # OPS factor: own OPS vs. league average 0.720
    ops_factor = max(0.65, min(1.35, offense["ops"] / 0.720))

    # Weighted composite using backtest coefficients
    composite = (
        _ERA_W  * era_factor  +
        _WHIP_W * whip_factor +
        _OPS_W  * ops_factor
    ) / _TOTAL_W

    # Base RPG adjusted by run differential signal when available
    base_rpg = offense["runs_per_game"]
    run_diff = offense.get("run_differential_per_game")
    if run_diff is not None:
        base_rpg += run_diff * 0.1

    noise = random.uniform(-2.0, 2.0)
    return max(0, round(base_rpg * composite + noise))


def run_monte_carlo(
    away_team: dict,
    home_team: dict,
    sim_count: int = 1000,
) -> dict:
    away_wins = 0
    home_wins = 0
    away_scores = []
    home_scores = []

    for _ in range(sim_count):
        away_runs = simulate_runs(offense=away_team, opponent=home_team)
        home_runs = simulate_runs(offense=home_team, opponent=away_team)

        away_scores.append(away_runs)
        home_scores.append(home_runs)

        if away_runs > home_runs:
            away_wins += 1
        elif home_runs > away_runs:
            home_wins += 1
        else:
            away_wins += 0.5
            home_wins += 0.5

    away_win_pct_raw = away_wins / sim_count
    home_win_pct_raw = home_wins / sim_count

    # Combine base home-field edge with venue-specific park factor.
    # Park factor is set by build_team_features(venue=...) on the home team;
    # positive values (e.g. Dodger Stadium +0.144) boost home win probability,
    # negative values (e.g. Guaranteed Rate Field -0.151) suppress it.
    park_factor = home_team.get("park_factor", 0.0)
    effective_hfa = HOME_FIELD_ADVANTAGE + park_factor
    home_win_pct = min(0.95, max(0.05, home_win_pct_raw + effective_hfa))
    away_win_pct = min(0.95, max(0.05, 1.0 - home_win_pct))

    projected_away_score = sum(away_scores) / sim_count
    projected_home_score = sum(home_scores) / sim_count
    projected_total = projected_away_score + projected_home_score
    confidence_score = abs(home_win_pct - away_win_pct) * 100

    recommended_side = "AWAY" if away_win_pct > home_win_pct else "HOME"

    return {
        "away_win_pct": round(away_win_pct, 4),
        "home_win_pct": round(home_win_pct, 4),
        "projected_away_score": round(projected_away_score, 2),
        "projected_home_score": round(projected_home_score, 2),
        "projected_total": round(projected_total, 2),
        "confidence_score": round(confidence_score, 2),
        "recommended_side": recommended_side,
        "sim_count": sim_count,
    }
