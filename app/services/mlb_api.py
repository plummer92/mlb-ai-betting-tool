from datetime import datetime
import requests

from app.config import MLB_API_BASE


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
                }
            )

    return games


def fetch_team_stats(team_id: int, season: int) -> dict:
    pitching_url = (
        f"{MLB_API_BASE}/teams/{team_id}/stats"
        f"?stats=season&group=pitching&season={season}"
    )
    hitting_url = (
        f"{MLB_API_BASE}/teams/{team_id}/stats"
        f"?stats=season&group=hitting&season={season}"
    )

    pitching_resp = requests.get(pitching_url, timeout=30)
    hitting_resp = requests.get(hitting_url, timeout=30)

    pitching_resp.raise_for_status()
    hitting_resp.raise_for_status()

    pitching_json = pitching_resp.json()
    hitting_json = hitting_resp.json()

    pitching_split = (
        pitching_json.get("stats", [{}])[0].get("splits", [{}])[0].get("stat", {})
    )
    hitting_split = (
        hitting_json.get("stats", [{}])[0].get("splits", [{}])[0].get("stat", {})
    )

    return {
        "era": float(pitching_split.get("era", 4.20) or 4.20),
        "whip": float(pitching_split.get("whip", 1.30) or 1.30),
        "avg": float(hitting_split.get("avg", 0.248) or 0.248),
        "ops": float(hitting_split.get("ops", 0.720) or 0.720),
        "home_runs": int(hitting_split.get("homeRuns", 180) or 180),
        "runs": int(hitting_split.get("runs", 700) or 700),
    }
