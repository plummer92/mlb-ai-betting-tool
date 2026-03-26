from datetime import datetime
import requests

from app.config import MLB_API_BASE

_INVALID_STAT = {"-.--", "-", "---", ".---", ""}


def _safe_float(value, default: float) -> float:
    """Convert an MLB API stat value to float, returning default for missing/invalid values."""
    if value is None or value in _INVALID_STAT:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_int(value, default: int) -> int:
    """Convert an MLB API stat value to int, returning default for missing/invalid values."""
    if value is None or value in _INVALID_STAT:
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def fetch_schedule_for_date(date_str: str) -> list[dict]:
    url = (
        f"{MLB_API_BASE}/schedule"
        f"?sportId=1&date={date_str}"
        f"&hydrate=team,linescore,probablePitcher,venue"
    )
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    payload = response.json()

    games = []
    for day in payload.get("dates", []):
        for g in day.get("games", []):
            game_date = day.get("date")
            game_time = g.get("gameDate")
            local_time = None
            if game_time:
                try:
                    local_time = datetime.fromisoformat(game_time.replace("Z", "+00:00")).isoformat()
                except ValueError:
                    local_time = game_time
            away_pitcher = g["teams"]["away"].get("probablePitcher") or {}
            home_pitcher = g["teams"]["home"].get("probablePitcher") or {}
            games.append({
                "game_id": g["gamePk"],
                "game_date": game_date,
                "season": int(game_date[:4]),
                "away_team": g["teams"]["away"]["team"]["name"],
                "home_team": g["teams"]["home"]["team"]["name"],
                "away_team_id": g["teams"]["away"]["team"]["id"],
                "home_team_id": g["teams"]["home"]["team"]["id"],
                "venue": g.get("venue", {}).get("name"),
                "status": g.get("status", {}).get("abstractGameState"),
                "start_time": local_time,
                "away_probable_pitcher": away_pitcher.get("fullName"),
                "away_pitcher_id": away_pitcher.get("id"),
                "home_probable_pitcher": home_pitcher.get("fullName"),
                "home_pitcher_id": home_pitcher.get("id"),
                "final_away_score": g["teams"]["away"].get("score"),
                "final_home_score": g["teams"]["home"].get("score"),
            })
    return games


def _fetch_team_stats_for_season(team_id: int, season: int):
    pitching_resp = requests.get(
        f"{MLB_API_BASE}/teams/{team_id}/stats?stats=season&group=pitching&season={season}",
        timeout=30
    )
    hitting_resp = requests.get(
        f"{MLB_API_BASE}/teams/{team_id}/stats?stats=season&group=hitting&season={season}",
        timeout=30
    )
    if pitching_resp.status_code == 404 or hitting_resp.status_code == 404:
        return None
    pitching_resp.raise_for_status()
    hitting_resp.raise_for_status()
    pitching_stats = pitching_resp.json().get("stats") or [{}]
    pitching_split = (pitching_stats[0].get("splits") or [{}])[0].get("stat", {})
    hitting_stats = hitting_resp.json().get("stats") or [{}]
    hitting_split = (hitting_stats[0].get("splits") or [{}])[0].get("stat", {})
    games_played = int(hitting_split.get("gamesPlayed", 0) or 0)
    if not pitching_split and not hitting_split:
        return None
    # Too few games to produce meaningful rates — caller will try prior season
    if games_played < 10:
        return None
    return {
        "era":          _safe_float(pitching_split.get("era"),          4.20),
        "whip":         _safe_float(pitching_split.get("whip"),         1.30),
        "runs_allowed": _safe_int(pitching_split.get("runsAllowed"),    700),
        "avg":          _safe_float(hitting_split.get("avg"),           0.248),
        "ops":          _safe_float(hitting_split.get("ops"),           0.720),
        "home_runs":    _safe_int(hitting_split.get("homeRuns"),        180),
        "runs":         _safe_int(hitting_split.get("runs"),            700),
        "games_played": games_played,
    }


def fetch_pitcher_stats(pitcher_id: int, season: int) -> dict | None:
    """
    Fetch season pitching stats for a specific pitcher.
    Returns ERA, WHIP, K/9, BB/9 or None if unavailable.
    Retries with season-1 if current season has no data yet.
    """
    def _fetch(pid: int, s: int) -> dict | None:
        resp = requests.get(
            f"{MLB_API_BASE}/people/{pid}/stats?stats=season&group=pitching&season={s}",
            timeout=30,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        stats_list = resp.json().get("stats") or [{}]
        split = (stats_list[0].get("splits") or [{}])[0].get("stat", {})
        if not split:
            return None
        return {
            "era":  _safe_float(split.get("era"),                 4.20),
            "whip": _safe_float(split.get("whip"),                1.30),
            "k9":   _safe_float(split.get("strikeoutsPer9Inn"),   8.5),
            "bb9":  _safe_float(split.get("walksPer9Inn"),        3.2),
        }

    return _fetch(pitcher_id, season) or _fetch(pitcher_id, season - 1)


def fetch_team_stats(team_id: int, season: int) -> dict:
    stats = _fetch_team_stats_for_season(team_id, season)
    if stats:
        return stats
    stats = _fetch_team_stats_for_season(team_id, season - 1)
    if stats:
        return stats
    return {
        "era": 4.20, "whip": 1.30, "runs_allowed": 700,
        "avg": 0.248, "ops": 0.720, "home_runs": 180, "runs": 700,
        "games_played": 0,
    }
