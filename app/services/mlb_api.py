import time
from datetime import datetime
import requests

from app.config import MLB_API_BASE

# In-process cache for pitcher stats — keyed by (pitcher_id, season)
_pitcher_cache: dict[tuple[int, int], dict] = {}


# ── Schedule ─────────────────────────────────────────────────────────────────

def _parse_weather(g: dict) -> dict:
    w = g.get("weather") or {}
    wind_str = w.get("wind", "") or ""

    wind_mph = None
    wind_dir = None
    if wind_str:
        parts = wind_str.split(",", 1)
        try:
            wind_mph = int(parts[0].replace("mph", "").strip().split()[0])
        except (ValueError, IndexError):
            pass
        wind_dir = parts[1].strip() if len(parts) > 1 else None

    return {
        "weather_condition": w.get("condition") or None,
        "weather_temp":      int(w["temp"]) if w.get("temp") else None,
        "weather_wind":      wind_str or None,
        "weather_wind_mph":  wind_mph,
        "weather_wind_dir":  wind_dir,
    }


def fetch_schedule_for_date(date_str: str) -> list[dict]:
    url = (
        f"{MLB_API_BASE}/schedule"
        f"?sportId=1&date={date_str}"
        f"&hydrate=team,linescore,probablePitcher,game(weather),venue"
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
                    local_time = datetime.fromisoformat(
                        game_time.replace("Z", "+00:00")
                    ).isoformat()
                except ValueError:
                    local_time = game_time

            games.append(
                {
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
                    "away_probable_pitcher": g["teams"]["away"].get("probablePitcher", {}).get("fullName"),
                    "home_probable_pitcher": g["teams"]["home"].get("probablePitcher", {}).get("fullName"),
                    "away_pitcher_id": g["teams"]["away"].get("probablePitcher", {}).get("id"),
                    "home_pitcher_id": g["teams"]["home"].get("probablePitcher", {}).get("id"),
                    "final_away_score": g["teams"]["away"].get("score"),
                    "final_home_score": g["teams"]["home"].get("score"),
                    **_parse_weather(g),
                }
            )

    return games


# ── Team stats ────────────────────────────────────────────────────────────────

def _fetch_split(team_id: int, group: str, season: int, game_type: str | None = None) -> dict | None:
    """Fetch one stat group for a team. Returns the stat dict or None on error/empty."""
    url = (
        f"{MLB_API_BASE}/teams/{team_id}/stats"
        f"?stats=season&group={group}&season={season}"
    )
    if game_type:
        url += f"&gameType={game_type}"
    resp = requests.get(url, timeout=30)
    if resp.status_code in (404, 503):
        return None
    resp.raise_for_status()
    split = resp.json().get("stats", [{}])[0].get("splits", [{}])[0].get("stat", {})
    return split if split else None


def _build_stats(pitching: dict, hitting: dict) -> dict:
    return {
        "era":       float(pitching.get("era", 4.20) or 4.20),
        "whip":      float(pitching.get("whip", 1.30) or 1.30),
        "avg":       float(hitting.get("avg", 0.248) or 0.248),
        "ops":       float(hitting.get("ops", 0.720) or 0.720),
        "home_runs": int(hitting.get("homeRuns", 180) or 180),
        "runs":      int(hitting.get("runs", 700) or 700),
    }


def _blend_stats(
    prior_p: dict, prior_h: dict,
    spring_p: dict, spring_h: dict,
    spring_weight: float = 0.20,
) -> dict:
    """Weighted blend: (1-w)*prior + w*spring for each numeric stat."""
    pw = spring_weight
    prior  = _build_stats(prior_p, prior_h)
    spring = _build_stats(spring_p, spring_h)
    return {
        "era":       round(prior["era"]  * (1 - pw) + spring["era"]  * pw, 3),
        "whip":      round(prior["whip"] * (1 - pw) + spring["whip"] * pw, 3),
        "avg":       round(prior["avg"]  * (1 - pw) + spring["avg"]  * pw, 4),
        "ops":       round(prior["ops"]  * (1 - pw) + spring["ops"]  * pw, 4),
        "home_runs": round(prior["home_runs"] * (1 - pw) + spring["home_runs"] * pw),
        "runs":      round(prior["runs"]      * (1 - pw) + spring["runs"]      * pw),
    }


def fetch_team_stats(team_id: int, season: int) -> dict:
    # 1. Current regular season
    pitching = _fetch_split(team_id, "pitching", season)
    hitting  = _fetch_split(team_id, "hitting",  season)
    if pitching and hitting:
        return _build_stats(pitching, hitting)

    # 2. Spring training blend: 20% spring current year + 80% prior season
    spring_p = _fetch_split(team_id, "pitching", season, game_type="S")
    spring_h = _fetch_split(team_id, "hitting",  season, game_type="S")
    prior_p  = _fetch_split(team_id, "pitching", season - 1)
    prior_h  = _fetch_split(team_id, "hitting",  season - 1)

    if spring_p and spring_h and prior_p and prior_h:
        return _blend_stats(prior_p, prior_h, spring_p, spring_h, spring_weight=0.20)

    # 3. Pure prior season
    if prior_p and prior_h:
        return _build_stats(prior_p, prior_h)

    # 4. League-average defaults
    return {"era": 4.20, "whip": 1.30, "avg": 0.248, "ops": 0.720, "home_runs": 180, "runs": 700}


# ── Pitcher stats ─────────────────────────────────────────────────────────────

def fetch_pitcher_stats(pitcher_id: int, season: int, _throttle: bool = True) -> dict:
    """
    Return season pitching stats for an individual pitcher.
    Results are cached in-process so each (pitcher_id, season) is fetched once.
    """
    key = (pitcher_id, season)
    if key in _pitcher_cache:
        return _pitcher_cache[key]

    if _throttle:
        time.sleep(0.05)  # be polite to the public MLB API

    url = (
        f"{MLB_API_BASE}/people/{pitcher_id}/stats"
        f"?stats=season&group=pitching&season={season}"
    )
    resp = requests.get(url, timeout=30)
    if resp.status_code in (404, 503):
        result = {"era": 4.50, "whip": 1.35, "innings_pitched": 0}
        _pitcher_cache[key] = result
        return result

    resp.raise_for_status()
    stat = resp.json().get("stats", [{}])[0].get("splits", [{}])[0].get("stat", {})
    result = {
        "era":             float(stat.get("era", 4.50) or 4.50),
        "whip":            float(stat.get("whip", 1.35) or 1.35),
        "innings_pitched": float(stat.get("inningsPitched", 0) or 0),
    }
    _pitcher_cache[key] = result
    return result


# ── Historical schedule (backtest) ────────────────────────────────────────────

def fetch_season_schedule(season: int) -> list[dict]:
    """
    Fetch every completed regular-season game for a full season.
    Fetches month-by-month to avoid oversized responses.
    Returns games with scores, pitcher IDs, and team IDs.
    """
    months = [
        (f"{season}-03-20", f"{season}-03-31"),  # opening series (late March)
        (f"{season}-04-01", f"{season}-04-30"),
        (f"{season}-05-01", f"{season}-05-31"),
        (f"{season}-06-01", f"{season}-06-30"),
        (f"{season}-07-01", f"{season}-07-31"),
        (f"{season}-08-01", f"{season}-08-31"),
        (f"{season}-09-01", f"{season}-09-30"),
        (f"{season}-10-01", f"{season}-10-15"),  # end-of-season / wild card
    ]

    all_games: list[dict] = []
    for start, end in months:
        url = (
            f"{MLB_API_BASE}/schedule"
            f"?sportId=1&startDate={start}&endDate={end}"
            f"&hydrate=team,linescore,probablePitcher"
            f"&gameType=R"  # regular season only
        )
        resp = requests.get(url, timeout=60)
        if resp.status_code in (404, 503):
            continue
        resp.raise_for_status()

        for day in resp.json().get("dates", []):
            for g in day.get("games", []):
                status = g.get("status", {}).get("abstractGameState", "")
                if status != "Final":
                    continue

                teams = g["teams"]
                home_score = teams["home"].get("score")
                away_score = teams["away"].get("score")
                if home_score is None or away_score is None:
                    continue

                all_games.append({
                    "game_id":          g["gamePk"],
                    "game_date":        day.get("date"),
                    "home_team_id":     teams["home"]["team"]["id"],
                    "away_team_id":     teams["away"]["team"]["id"],
                    "home_team":        teams["home"]["team"]["name"],
                    "away_team":        teams["away"]["team"]["name"],
                    "venue":            g.get("venue", {}).get("name"),
                    "home_score":       int(home_score),
                    "away_score":       int(away_score),
                    "home_starter_id":  teams["home"].get("probablePitcher", {}).get("id"),
                    "away_starter_id":  teams["away"].get("probablePitcher", {}).get("id"),
                    "home_starter_name": teams["home"].get("probablePitcher", {}).get("fullName"),
                    "away_starter_name": teams["away"].get("probablePitcher", {}).get("fullName"),
                })

    return all_games


# ── Standings ─────────────────────────────────────────────────────────────────

def fetch_all_team_records(season: int) -> dict[int, dict]:
    """Return {team_id: {"wins": int, "losses": int}} for all MLB teams."""
    url = f"{MLB_API_BASE}/standings?leagueId=103,104&season={season}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    records: dict[int, dict] = {}
    for division in resp.json().get("records", []):
        for tr in division.get("teamRecords", []):
            team_id = tr["team"]["id"]
            records[team_id] = {
                "wins":   int(tr.get("wins", 0)),
                "losses": int(tr.get("losses", 0)),
            }
    return records


def fetch_full_standings(season: int) -> dict[int, dict]:
    """
    Extended standings including run differential and win percentage.
    Returns {team_id: {wins, losses, win_pct, run_diff, runs_scored, runs_allowed}}
    """
    url = f"{MLB_API_BASE}/standings?leagueId=103,104&season={season}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    standings: dict[int, dict] = {}
    for division in resp.json().get("records", []):
        for tr in division.get("teamRecords", []):
            team_id = tr["team"]["id"]
            wins    = int(tr.get("wins", 0))
            losses  = int(tr.get("losses", 0))
            gp      = wins + losses
            standings[team_id] = {
                "wins":         wins,
                "losses":       losses,
                "win_pct":      wins / gp if gp > 0 else 0.5,
                "run_diff":     int(tr.get("runDifferential", 0)),
                "runs_scored":  int(tr.get("runsScored", 0)),
                "runs_allowed": int(tr.get("runsAllowed", 0)),
            }
    return standings
