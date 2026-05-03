"""
v0.5 signal backtest — evaluates how travel stress, series position, and
public-bias signals correlate with actual game outcomes stored in
game_outcomes_review.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.schema import Game, GameOutcomeReview
from app.services.travel_service import calculate_travel_stress
from app.services.series_service import get_series_position, get_public_bias_edge


def _is_weekend(game_date: date) -> bool:
    return game_date.weekday() in (4, 5, 6)  # Fri/Sat/Sun


def _local_hour(start_time: Optional[str], home_team_id: Optional[int]) -> int:
    """Return local start hour (0-23); defaults to 19 if unparseable."""
    from app.services.travel_service import TIMEZONE_MAP
    if not start_time:
        return 19
    try:
        ts = start_time.replace("Z", "+00:00")
        utc_dt = datetime.fromisoformat(ts)
        offset = TIMEZONE_MAP.get(home_team_id, -5) if home_team_id else -5
        return (utc_dt.hour + offset) % 24
    except Exception:
        return 19


def _pct(wins: int, total: int) -> float:
    return round(wins / total, 4) if total else 0.0


def _vs_baseline(signal_rate: float, baseline_rate: float) -> str:
    diff = (signal_rate - baseline_rate) * 100
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff:.1f}%"


def run_signal_backtest(db: Session, start_date: date, end_date: date) -> dict:
    """
    Back-test v0.5 signals against GameOutcomeReview records in [start_date, end_date].

    Only reviews that have a linked Game record (with team IDs) are included.
    """
    reviews = (
        db.query(GameOutcomeReview)
        .filter(
            GameOutcomeReview.game_date >= start_date,
            GameOutcomeReview.game_date <= end_date,
        )
        .order_by(GameOutcomeReview.game_date)
        .all()
    )

    # Prefetch all games for the date range into a dict keyed by game_id
    game_ids = [r.game_id for r in reviews]
    games: dict[int, Game] = {}
    if game_ids:
        for g in db.query(Game).filter(Game.game_id.in_(game_ids)).all():
            games[g.game_id] = g

    # ── Bucket accumulators ──────────────────────────────────────────────────
    buckets: dict[str, dict] = {
        "high_travel_away": {"games": 0, "away_wins": 0, "home_wins": 0, "total_runs": 0.0},
        "series_opener":    {"games": 0, "away_wins": 0, "home_wins": 0, "total_runs": 0.0},
        "series_finale":    {"games": 0, "away_wins": 0, "home_wins": 0, "total_runs": 0.0},
        "weekend_night":    {"games": 0, "away_wins": 0, "home_wins": 0, "total_runs": 0.0},
        "weekday":          {"games": 0, "away_wins": 0, "home_wins": 0, "total_runs": 0.0},
    }

    baseline = {"games": 0, "away_wins": 0, "home_wins": 0, "total_runs": 0.0}

    analyzed = 0

    for review in reviews:
        game = games.get(review.game_id)
        if game is None:
            continue
        if game.away_team_id is None or game.home_team_id is None:
            continue

        # Actual outcome
        away_score = review.final_away_score
        home_score = review.final_home_score
        if away_score is None or home_score is None:
            continue

        total_runs = away_score + home_score
        away_won = 1 if away_score > home_score else 0
        home_won = 1 - away_won

        analyzed += 1
        baseline["games"] += 1
        baseline["away_wins"] += away_won
        baseline["home_wins"] += home_won
        baseline["total_runs"] += total_runs

        game_date: date = review.game_date
        start_time: Optional[str] = game.start_time

        # ── Signal computation ───────────────────────────────────────────────
        travel_stress = calculate_travel_stress(game.away_team_id, game_date, db)

        series_pos = get_series_position(game.home_team_id, game_date, db)
        is_opener = series_pos.get("is_series_opener", False)
        is_finale = series_pos.get("is_series_finale", False)

        weekend = _is_weekend(game_date)
        local_hr = _local_hour(start_time, game.home_team_id)

        # ── Bucket classification ────────────────────────────────────────────
        def _add(bucket: str) -> None:
            buckets[bucket]["games"] += 1
            buckets[bucket]["away_wins"] += away_won
            buckets[bucket]["home_wins"] += home_won
            buckets[bucket]["total_runs"] += total_runs

        if travel_stress > 0.35:
            _add("high_travel_away")

        if is_opener:
            _add("series_opener")

        if is_finale:
            _add("series_finale")

        if weekend and local_hr >= 17:
            _add("weekend_night")

        if not weekend:
            _add("weekday")

    # ── Compute baseline rates ───────────────────────────────────────────────
    bl_games = baseline["games"]
    bl_home_wr = _pct(baseline["home_wins"], bl_games)
    bl_away_wr = _pct(baseline["away_wins"], bl_games)
    bl_avg_runs = round(baseline["total_runs"] / bl_games, 2) if bl_games else 0.0

    # ── Format signal results ────────────────────────────────────────────────
    def _format_bucket(key: str, b: dict, insight_template: str) -> dict:
        n = b["games"]
        away_wr = _pct(b["away_wins"], n)
        home_wr = _pct(b["home_wins"], n)
        avg_runs = round(b["total_runs"] / n, 2) if n else 0.0
        return {
            "games": n,
            "away_win_rate": away_wr,
            "home_win_rate": home_wr,
            "avg_total_runs": avg_runs,
            "vs_baseline_away": _vs_baseline(away_wr, bl_away_wr),
            "vs_baseline_home": _vs_baseline(home_wr, bl_home_wr),
            "insight": insight_template.format(
                away_wr=round(away_wr * 100, 1),
                home_wr=round(home_wr * 100, 1),
                bl_away=round(bl_away_wr * 100, 1),
                bl_home=round(bl_home_wr * 100, 1),
                avg_runs=avg_runs,
                n=n,
            ),
        }

    signals = {
        "high_travel_away": _format_bucket(
            "high_travel_away",
            buckets["high_travel_away"],
            "Away teams with high travel stress ({n} games): away win {away_wr}% vs baseline {bl_away}%; "
            "avg {avg_runs} total runs",
        ),
        "series_opener": _format_bucket(
            "series_opener",
            buckets["series_opener"],
            "Series openers ({n} games): home win {home_wr}% vs baseline {bl_home}%; "
            "avg {avg_runs} total runs (visitor just arrived)",
        ),
        "series_finale": _format_bucket(
            "series_finale",
            buckets["series_finale"],
            "Series finales ({n} games): away win {away_wr}% vs baseline {bl_away}%; "
            "avg {avg_runs} total runs (bullpen fatigue expected)",
        ),
        "weekend_night": _format_bucket(
            "weekend_night",
            buckets["weekend_night"],
            "Weekend night games after 17:00 local ({n} games): home win {home_wr}% vs baseline {bl_home}%; "
            "avg {avg_runs} total runs",
        ),
        "weekday": _format_bucket(
            "weekday",
            buckets["weekday"],
            "Weekday games ({n} games): away win {away_wr}% vs baseline {bl_away}%; "
            "avg {avg_runs} total runs (sharp-money environment)",
        ),
    }

    return {
        "date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "total_games_analyzed": analyzed,
        "baseline": {
            "home_win_rate": bl_home_wr,
            "away_win_rate": bl_away_wr,
            "avg_total_runs": bl_avg_runs,
        },
        "signals": signals,
    }
