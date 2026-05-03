"""
v0.5 Series position tracker.

Determines where a team sits within their current series (opener, middle,
finale). Never raises — returns safe defaults on any error.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.schema import Game
from app.services.travel_service import TIMEZONE_MAP

_DEFAULT_POSITION: dict = {
    "series_game_number": 1,
    "series_length": 1,
    "is_series_opener": True,
    "is_series_finale": True,
    "days_in_current_city": 1,
}


def get_series_position(team_id: int, game_date: date, db: Session) -> dict:
    """
    Return series context for team_id on game_date.

    Scans the games table for consecutive games (gap ≤ 1 day) at the same
    home_team_id (location) to build the series cluster, then returns where
    today's game falls within it.
    """
    try:
        today_game = (
            db.query(Game)
            .filter(
                Game.game_date == game_date,
                or_(Game.home_team_id == team_id, Game.away_team_id == team_id),
            )
            .first()
        )
        if today_game is None or today_game.home_team_id is None:
            return dict(_DEFAULT_POSITION)

        location_id = today_game.home_team_id

        location_games = (
            db.query(Game)
            .filter(
                Game.home_team_id == location_id,
                or_(Game.home_team_id == team_id, Game.away_team_id == team_id),
            )
            .order_by(Game.game_date)
            .all()
        )

        dates = [g.game_date for g in location_games]
        try:
            today_idx = dates.index(game_date)
        except ValueError:
            return dict(_DEFAULT_POSITION)

        # Expand backward through consecutive days (gap ≤ 1)
        series_start = today_idx
        while series_start > 0:
            if (dates[series_start] - dates[series_start - 1]).days <= 1:
                series_start -= 1
            else:
                break

        # Expand forward through consecutive days (gap ≤ 1)
        series_end = today_idx
        while series_end < len(dates) - 1:
            if (dates[series_end + 1] - dates[series_end]).days <= 1:
                series_end += 1
            else:
                break

        series_game_number = today_idx - series_start + 1
        series_length = series_end - series_start + 1

        return {
            "series_game_number": series_game_number,
            "series_length": series_length,
            "is_series_opener": series_game_number == 1,
            "is_series_finale": series_game_number == series_length,
            "days_in_current_city": series_game_number,
        }

    except Exception:
        return dict(_DEFAULT_POSITION)


def get_public_bias_edge(
    game_date: date,
    start_time: Optional[str],
    home_win_pct: float,
    away_win_pct: float,
    home_team_id: Optional[int],
) -> float:
    """
    Home-dog / public-fade factor based on game timing and win probability.

    Public money on weekends skews toward the visible away favorite, leaving
    the home underdog undervalued. Midweek sharp action punishes the weaker
    team without the crowd-noise mask.
    """
    try:
        home_is_underdog = home_win_pct < 0.45
        day = game_date.weekday()          # 0=Mon … 4=Fri, 5=Sat, 6=Sun
        is_weekend = day in (4, 5, 6)

        # Convert UTC start_time to local hour at home park
        hour = 19  # safe default (evening game)
        if start_time and home_team_id:
            ts = start_time.replace("Z", "+00:00")
            utc_dt = datetime.fromisoformat(ts)
            utc_offset = TIMEZONE_MAP.get(home_team_id, -5)
            hour = (utc_dt.hour + utc_offset) % 24

        if home_is_underdog and is_weekend and hour >= 17:
            return +0.06   # public backs away favorite, home dog undervalued
        if home_is_underdog and not is_weekend:
            return -0.04   # no crowd boost, sharp money, better team wins
        if not home_is_underdog and is_weekend and hour >= 17:
            return -0.02   # home favorites slightly underperform on weekend nights
        return 0.0
    except Exception:
        return 0.0


def get_travel_over_edge(travel_stress_away: float) -> float:
    """
    Run boost for high-travel away teams — an OVER signal.

    High travel stress games (>0.35) average 10.07 runs vs 8.76 baseline.
    """
    if travel_stress_away > 0.35:
        return 0.30
    return 0.0


def get_series_opener_edge(series_position: dict) -> float:
    """
    Run suppression modifier based on series position.

    Game 1 (opener): -0.20  home team advantage, visitor just arrived
    Game 2:           0.0   neutral
    Game 3+:         +0.05  bullpen fatigue signal is weak
    """
    game_num = series_position.get("series_game_number")
    if game_num is None:
        return 0.0
    if game_num == 1:
        return -0.20
    if game_num == 2:
        return 0.0
    return 0.05
