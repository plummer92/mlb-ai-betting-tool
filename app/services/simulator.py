import math
from datetime import date, datetime

import numpy as np

LEAGUE_AVG_RPG = 4.4
EARLY_SEASON_TOTAL_FACTOR = 0.82  # pitchers ahead of hitters in April/May
_OPS_W = 0.32
_RUN_DIFF_W = 0.26
_PYTHAG_W = 0.22
_WHIP_W = 0.12
_KBB_W = 0.08
_TOTAL_W = _OPS_W + _RUN_DIFF_W + _PYTHAG_W + _WHIP_W + _KBB_W

HOME_FIELD_RUNS = 0.12
PROBABILITY_SHRINK = 0.68
FINAL_PROBABILITY_FLOOR = 0.05
FINAL_PROBABILITY_CEILING = 0.95

MODEL_VERSION = "v0.4-early-season-adj"


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def set_weights(ops_w: float, run_diff_w: float, pythag_w: float) -> None:
    """Update simulator macro weights from the latest backtest regression."""
    global _OPS_W, _RUN_DIFF_W, _PYTHAG_W, _TOTAL_W
    _OPS_W = ops_w
    _RUN_DIFF_W = run_diff_w
    _PYTHAG_W = pythag_w
    _TOTAL_W = _OPS_W + _RUN_DIFF_W + _PYTHAG_W + _WHIP_W + _KBB_W


def _offense_strength(team: dict) -> float:
    ops_factor = _clamp(float(team.get("ops", 0.720)) / 0.720, 0.92, 1.08)
    run_diff = float(team.get("run_differential_per_game") or 0.0)
    run_diff_factor = _clamp(1.0 + (run_diff * 0.06), 0.90, 1.10)
    pythag = float(team.get("pythagorean_win_pct") or 0.5)
    pythag_factor = _clamp(1.0 + ((pythag - 0.5) * 0.80), 0.92, 1.08)
    whip_factor = _clamp(1.0 + ((1.30 - float(team.get("team_whip", 1.30))) * 0.18), 0.95, 1.05)
    kbb_factor = _clamp(1.0 + ((float(team.get("starter_kbb_percent") or 0.12) - 0.12) * 0.80), 0.95, 1.05)
    return (
        (_OPS_W * ops_factor)
        + (_RUN_DIFF_W * run_diff_factor)
        + (_PYTHAG_W * pythag_factor)
        + (_WHIP_W * whip_factor)
        + (_KBB_W * kbb_factor)
    ) / _TOTAL_W


def _opponent_run_suppression(opponent: dict) -> float:
    starter_run_prevention = opponent.get("starter_run_prevention")
    starter_factor = 1.0
    if starter_run_prevention is not None:
        starter_factor = _clamp(1.0 + ((float(starter_run_prevention) - 4.10) * 0.06), 0.90, 1.10)

    whip_factor = _clamp(1.0 + ((float(opponent.get("starter_whip", 1.28)) - 1.28) * 0.20), 0.92, 1.08)
    kbb_pct = float(opponent.get("starter_kbb_percent") or 0.12)
    kbb_factor = _clamp(1.0 - ((kbb_pct - 0.12) * 0.90), 0.92, 1.08)
    bullpen_metric = opponent.get("bullpen_run_prevention")
    bullpen_factor = 1.0
    if bullpen_metric is not None:
        bullpen_factor = _clamp(1.0 + ((float(bullpen_metric) - 4.05) * 0.04), 0.94, 1.06)

    return (starter_factor * 0.40) + (whip_factor * 0.20) + (kbb_factor * 0.20) + (bullpen_factor * 0.20)


def _expected_runs(offense: dict, opponent: dict, *, park_run_factor: float, is_home: bool) -> float:
    base_runs = LEAGUE_AVG_RPG * _offense_strength(offense) * _opponent_run_suppression(opponent)
    projected = base_runs * park_run_factor
    if is_home:
        projected += HOME_FIELD_RUNS
    return _clamp(projected, 2.8, 6.6)


def _shrink_probability(probability: float) -> float:
    return 0.5 + ((probability - 0.5) * PROBABILITY_SHRINK)


def _blend(probability: float, anchor: float, weight: float) -> float:
    return (probability * (1.0 - weight)) + (anchor * weight)


def _apply_market_anchor(probability: float, market_probability: float | None) -> tuple[float, float | None]:
    if market_probability is None:
        return probability, None
    market_probability = _clamp(float(market_probability), 0.05, 0.95)
    deviation = abs(probability - market_probability)
    weight = _clamp(0.18 + (deviation * 1.6), 0.18, 0.45)
    return _blend(probability, market_probability, weight), round(probability - market_probability, 4)


def run_monte_carlo(
    away_team: dict,
    home_team: dict,
    sim_count: int = 1000,
    *,
    market_home_prob: float | None = None,
    logistic_home_prob: float | None = None,
    game_date: "date | str | None" = None,
) -> dict:
    away_scores = []
    home_scores = []

    park_run_factor = float(home_team.get("park_run_factor", 1.0) or 1.0)
    away_lambda = _expected_runs(away_team, home_team, park_run_factor=park_run_factor, is_home=False)
    home_lambda = _expected_runs(home_team, away_team, park_run_factor=park_run_factor, is_home=True)

    print(
        f"[simulator] total_inputs "
        f"park_run_factor={park_run_factor:.4f} "
        f"away_starter_run_prevention={away_team.get('starter_run_prevention')} "
        f"home_starter_run_prevention={home_team.get('starter_run_prevention')} "
        f"away_starter_whip={away_team.get('starter_whip')} "
        f"home_starter_whip={home_team.get('starter_whip')} "
        f"away_starter_kbb_pct={away_team.get('starter_kbb_percent')} "
        f"home_starter_kbb_pct={home_team.get('starter_kbb_percent')} "
        f"away_bullpen_rp={away_team.get('bullpen_run_prevention')} "
        f"home_bullpen_rp={home_team.get('bullpen_run_prevention')} "
        f"away_lambda={away_lambda:.3f} home_lambda={home_lambda:.3f} "
        f"projected_total={away_lambda + home_lambda:.2f}",
        flush=True,
    )

    away_runs = np.random.poisson(away_lambda, size=sim_count)
    home_runs = np.random.poisson(home_lambda, size=sim_count)
    away_scores.extend(int(x) for x in away_runs.tolist())
    home_scores.extend(int(x) for x in home_runs.tolist())

    away_wins = float(np.sum(away_runs > home_runs) + (0.5 * np.sum(away_runs == home_runs)))
    home_wins = float(np.sum(home_runs > away_runs) + (0.5 * np.sum(home_runs == away_runs)))

    home_win_pct_raw = home_wins / sim_count
    home_win_pct = _shrink_probability(home_win_pct_raw)

    logistic_delta = None
    if logistic_home_prob is not None:
        logistic_home_prob = _clamp(float(logistic_home_prob), 0.05, 0.95)
        logistic_delta = round(home_win_pct - logistic_home_prob, 4)
        home_win_pct = _blend(home_win_pct, logistic_home_prob, 0.22)

    home_win_pct, market_delta = _apply_market_anchor(home_win_pct, market_home_prob)
    home_win_pct = _clamp(home_win_pct, FINAL_PROBABILITY_FLOOR, FINAL_PROBABILITY_CEILING)
    away_win_pct = _clamp(1.0 - home_win_pct, FINAL_PROBABILITY_FLOOR, FINAL_PROBABILITY_CEILING)

    early_season_adj = 1.0
    if game_date is not None:
        try:
            gd = game_date if isinstance(game_date, date) else datetime.fromisoformat(str(game_date)).date()
            if gd < date(gd.year, 6, 1):
                early_season_adj = EARLY_SEASON_TOTAL_FACTOR
        except (ValueError, TypeError):
            pass

    projected_away_score = (sum(away_scores) / sim_count) * early_season_adj
    projected_home_score = (sum(home_scores) / sim_count) * early_season_adj
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
        "market_home_prob": round(market_home_prob, 4) if market_home_prob is not None else None,
        "market_delta": market_delta,
        "logistic_home_prob": round(logistic_home_prob, 4) if logistic_home_prob is not None else None,
        "logistic_delta": logistic_delta,
        "away_lambda": round(away_lambda, 3),
        "home_lambda": round(home_lambda, 3),
        "early_season_adj": round(early_season_adj, 2),
    }
