"""
Backtest data collector.

Fetches historical MLB games (2022-2024) from the MLB Stats API and stores
them in backtest_games with all features needed for logistic regression.

Uses full-season team stats (not point-in-time) — a known simplification
that's acceptable for a first regression pass.

Collection is idempotent: already-stored game_ids are skipped.
"""

from datetime import date

from sqlalchemy.orm import Session

from app.models.schema import BacktestGame
from app.services.mlb_api import (
    _fetch_split,
    _build_stats,
    fetch_full_standings,
    fetch_pitcher_stats,
    fetch_season_schedule,
)

# League-average fallbacks
_DEFAULT_ERA  = 4.20
_DEFAULT_OPS  = 0.720
_DEFAULT_WHIP = 1.30


def _team_stats_for_season(season: int) -> dict[int, dict]:
    """
    Build {team_id: {era, ops, whip}} for all 30 teams in one season.
    Fetches each team's pitching + hitting splits and caches the result.
    Only called once per season during a collection run.
    """
    from app.services.mlb_api import MLB_API_BASE
    import requests

    # Get all team IDs from standings so we know who to fetch
    standings_url = f"{MLB_API_BASE}/standings?leagueId=103,104&season={season}"
    resp = requests.get(standings_url, timeout=30)
    resp.raise_for_status()

    team_ids: list[int] = []
    for div in resp.json().get("records", []):
        for tr in div.get("teamRecords", []):
            team_ids.append(tr["team"]["id"])

    stats: dict[int, dict] = {}
    for team_id in team_ids:
        pitching = _fetch_split(team_id, "pitching", season)
        hitting  = _fetch_split(team_id, "hitting",  season)
        if pitching and hitting:
            built = _build_stats(pitching, hitting)
            stats[team_id] = {
                "era":  built["era"],
                "ops":  built["ops"],
                "whip": built["whip"],
            }
        else:
            stats[team_id] = {"era": _DEFAULT_ERA, "ops": _DEFAULT_OPS, "whip": _DEFAULT_WHIP}

    return stats


def collect_season(season: int, db: Session) -> dict:
    """
    Fetch and store all completed regular-season games for one season.
    Returns {"season": season, "stored": N, "skipped": N, "errors": N}
    """
    print(f"[backtest] Collecting season {season}...")

    # --- Step 1: fetch full schedule for the season -------------------------
    games = fetch_season_schedule(season)
    print(f"[backtest] {len(games)} completed games found for {season}")

    # --- Step 2: pre-fetch season-level data (one-time per season) ----------
    print(f"[backtest] Fetching team stats for {season}...")
    team_stats  = _team_stats_for_season(season)

    print(f"[backtest] Fetching standings for {season}...")
    standings   = fetch_full_standings(season)

    # --- Step 3: process each game ------------------------------------------
    existing_ids: set[int] = {
        row[0] for row in db.query(BacktestGame.game_id)
        .filter(BacktestGame.season == season).all()
    }

    stored = skipped = errors = 0

    for g in games:
        gid = g["game_id"]
        if gid in existing_ids:
            skipped += 1
            continue

        try:
            home_id = g["home_team_id"]
            away_id = g["away_team_id"]

            h_stats   = team_stats.get(home_id, {"era": _DEFAULT_ERA, "ops": _DEFAULT_OPS, "whip": _DEFAULT_WHIP})
            a_stats   = team_stats.get(away_id, {"era": _DEFAULT_ERA, "ops": _DEFAULT_OPS, "whip": _DEFAULT_WHIP})
            h_stand   = standings.get(home_id, {"win_pct": 0.5, "run_diff": 0})
            a_stand   = standings.get(away_id, {"win_pct": 0.5, "run_diff": 0})

            # Starter ERA — fetch individually (cached after first call)
            h_pitcher_era = _DEFAULT_ERA
            a_pitcher_era = _DEFAULT_ERA
            if g["home_starter_id"]:
                h_pitcher_era = fetch_pitcher_stats(g["home_starter_id"], season)["era"]
            if g["away_starter_id"]:
                a_pitcher_era = fetch_pitcher_stats(g["away_starter_id"], season)["era"]

            home_win = g["home_score"] > g["away_score"]

            bg = BacktestGame(
                game_id           = gid,
                game_date         = date.fromisoformat(g["game_date"]),
                season            = season,
                home_team_id      = home_id,
                away_team_id      = away_id,
                home_team         = g["home_team"],
                away_team         = g["away_team"],
                venue             = g["venue"],
                home_score        = g["home_score"],
                away_score        = g["away_score"],
                home_win          = home_win,
                home_starter_id   = g["home_starter_id"],
                away_starter_id   = g["away_starter_id"],
                home_starter_name = g["home_starter_name"],
                away_starter_name = g["away_starter_name"],
                home_starter_era  = h_pitcher_era,
                away_starter_era  = a_pitcher_era,
                home_team_era     = h_stats["era"],
                away_team_era     = a_stats["era"],
                home_team_ops     = h_stats["ops"],
                away_team_ops     = a_stats["ops"],
                home_team_whip    = h_stats["whip"],
                away_team_whip    = a_stats["whip"],
                home_win_pct      = h_stand["win_pct"],
                away_win_pct      = a_stand["win_pct"],
                home_run_diff     = h_stand["run_diff"],
                away_run_diff     = a_stand["run_diff"],
            )
            db.add(bg)

            stored += 1
            if stored % 100 == 0:
                db.commit()
                print(f"[backtest]   {stored} games stored so far...")

        except Exception as exc:
            errors += 1
            print(f"[backtest] Error on game {gid}: {exc}")

    db.commit()
    print(f"[backtest] Season {season} done — stored={stored} skipped={skipped} errors={errors}")
    return {"season": season, "stored": stored, "skipped": skipped, "errors": errors}
