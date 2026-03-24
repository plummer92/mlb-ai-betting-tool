from datetime import datetime
import requests

from app.config import MLB_API_BASE


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
