"""
Statcast service — fetches advanced metrics from Baseball Savant.

Caching strategy:
  - One HTTP request per season fetches the full league leaderboard and
    stores it in .statcast_cache/{kind}_{season}.json.
  - Subsequent calls within the same process use the in-memory dict;
    after a restart, the disk cache is read instead of re-fetching.
  - All public functions return None on any failure so the main prediction
    pipeline can fall back to MLB Stats API values without interruption.

Rate limiting:
  - _REQUEST_DELAY (1 s) is applied before every live HTTP request.
  - Baseball Savant is fetched at most once per (kind, season) pair.
"""
import json
import time
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────────────

_CACHE_DIR = Path(".statcast_cache")
_REQUEST_DELAY = 1.0      # seconds between live HTTP requests
_TIMEOUT = 20             # seconds per request

_SAVANT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,*/*",
}

# In-process season caches — {season: {str(player_id or team_id): data}}
_pitcher_cache: dict[int, dict] = {}
_team_batting_cache: dict[int, dict] = {}
_team_sprint_cache: dict[int, dict] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cache_path(kind: str, season: int) -> Path:
    _CACHE_DIR.mkdir(exist_ok=True)
    return _CACHE_DIR / f"{kind}_{season}.json"


def _safe_float(val) -> float | None:
    if val is None or str(val).strip() in ("", "null", "None", "-.--", "-"):
        return None
    try:
        f = float(val)
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None


def _parse_csv(text: str) -> list[dict]:
    """Parse a comma-separated text into a list of row dicts."""
    # Strip UTF-8 BOM (\ufeff) that Baseball Savant prepends to CSV responses.
    text = text.lstrip("\ufeff")
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    headers = [h.strip('"').strip() for h in lines[0].split(",")]
    rows = []
    for line in lines[1:]:
        # Naive split — adequate for the numeric Baseball Savant CSVs
        values = [v.strip('"').strip() for v in line.split(",")]
        if len(values) >= len(headers):
            rows.append(dict(zip(headers, values)))
    return rows


def _load_from_disk(kind: str, season: int) -> dict | None:
    path = _cache_path(kind, season)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"[statcast] Disk cache read error ({kind} {season}): {e}", flush=True)
        return None


def _save_to_disk(kind: str, season: int, data: dict) -> None:
    try:
        with open(_cache_path(kind, season), "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[statcast] Disk cache write error ({kind} {season}): {e}", flush=True)


# ── Pitcher xERA ──────────────────────────────────────────────────────────────

def _load_pitcher_season(season: int) -> dict:
    """
    Return {str(pitcher_id): {xera, k_percent, bb_percent, barrel_rate}} for
    all qualified pitchers in a given season.  Fetches once, then caches.
    """
    if season in _pitcher_cache:
        return _pitcher_cache[season]

    disk_data = _load_from_disk("pitcher_xera", season)
    if disk_data is not None:
        _pitcher_cache[season] = disk_data
        print(
            f"[statcast] Pitcher xERA {season}: loaded {len(disk_data)} entries from disk",
            flush=True,
        )
        return disk_data

    # ── Live fetch ────────────────────────────────────────────────────────────
    data: dict = {}
    try:
        time.sleep(_REQUEST_DELAY)
        # Use min=10 so early-season (2026) and partial seasons still return rows.
        # "qualified" (min=q) requires ~162 IP which many starters never reach.
        url = (
            "https://baseballsavant.mlb.com/leaderboard/custom"
            f"?year={season}&type=pitcher&filter=&min=10"
            "&selections=xera%2Cp_k_percent%2Cp_bb_percent%2Cbarrel_batted_rate"
            "&chart=false&x=xera&y=xera&r=no&chartType=beeswarm&csv=true"
        )
        print(f"[statcast] Fetching pitcher xERA leaderboard: {url}", flush=True)
        resp = requests.get(url, timeout=_TIMEOUT, headers=_SAVANT_HEADERS)

        if resp.status_code != 200:
            print(
                f"[statcast] Pitcher leaderboard HTTP {resp.status_code} for {season}",
                flush=True,
            )
            _pitcher_cache[season] = data
            return data

        # Debug: show first 500 chars and header row so we can see the shape
        body_preview = resp.text[:500].replace("\n", "\\n")
        print(f"[statcast] Response preview ({season}): {body_preview}", flush=True)

        rows = _parse_csv(resp.text)
        print(
            f"[statcast] CSV rows parsed: {len(rows)} "
            f"| columns: {list(rows[0].keys()) if rows else 'none'}",
            flush=True,
        )

        for row in rows:
            pid = (
                row.get("player_id")
                or row.get("pitcher_id")
                or row.get("mlb_id")
            )
            if not pid:
                continue
            # The confirmed column name is "xera" (not "p_xera")
            xera = _safe_float(row.get("xera") or row.get("p_xera") or row.get("est_era"))
            if xera is None:
                continue
            data[str(pid)] = {
                "xera":        xera,
                "k_percent":   _safe_float(row.get("p_k_percent")  or row.get("k_percent")),
                "bb_percent":  _safe_float(row.get("p_bb_percent") or row.get("bb_percent")),
                "barrel_rate": _safe_float(row.get("barrel_batted_rate") or row.get("barrel_rate")),
            }

        print(
            f"[statcast] Fetched xERA for {len(data)} pitchers in {season}",
            flush=True,
        )
        if len(data) == 0:
            print(
                f"[statcast] 0 pitchers parsed for {season} — season may be too early; "
                "individual lookups will fall back to prior season automatically.",
                flush=True,
            )
        _save_to_disk("pitcher_xera", season, data)

    except Exception as e:
        print(
            f"[statcast] Pitcher leaderboard fetch failed ({season}): {e}",
            flush=True,
        )

    _pitcher_cache[season] = data
    return data


def fetch_pitcher_xera(pitcher_id: int, season: int) -> dict | None:
    """
    Return {xera, k_percent, bb_percent, barrel_rate} for a pitcher.
    Falls back to prior season if current season has no data (early-season).
    Returns None if unavailable.
    """
    try:
        for s in (season, season - 1):
            result = _load_pitcher_season(s).get(str(pitcher_id))
            if result:
                return result
        return None
    except Exception as e:
        print(f"[statcast] fetch_pitcher_xera({pitcher_id}, {season}): {e}", flush=True)
        return None


# ── Team Statcast batting ─────────────────────────────────────────────────────

def _load_team_batting_season(season: int) -> dict:
    """
    Download per-batter Statcast data for a season, aggregate to team level.
    Returns {str(team_id): {exit_velocity_avg, barrel_rate, hard_hit_rate}}.
    One HTTP request per season; all 30 teams populated at once.
    """
    if season in _team_batting_cache:
        return _team_batting_cache[season]

    disk_data = _load_from_disk("team_batting", season)
    if disk_data is not None:
        _team_batting_cache[season] = disk_data
        print(
            f"[statcast] Team batting {season}: loaded {len(disk_data)} teams from disk",
            flush=True,
        )
        return disk_data

    data: dict = {}
    try:
        time.sleep(_REQUEST_DELAY)
        # group_by=team_id asks Baseball Savant to aggregate server-side (one row
        # per team). If that returns no team_id column we fall back to per-batter
        # aggregation below using whatever team column is present.
        url = (
            "https://baseballsavant.mlb.com/leaderboard/custom"
            f"?year={season}&type=batter&filter=&min=q"
            "&selections=exit_velocity_avg%2Cbarrel_batted_rate%2Chard_hit_percent"
            "&chart=false&group_by=team_id&csv=true"
        )
        print(f"[statcast] Fetching team batting Statcast: {url}", flush=True)
        resp = requests.get(url, timeout=_TIMEOUT, headers=_SAVANT_HEADERS)

        print(
            f"[statcast] Team batting response preview ({season}): "
            f"{resp.text[:200].replace(chr(10), '\\n')}",
            flush=True,
        )

        if resp.status_code != 200:
            print(
                f"[statcast] Team batting HTTP {resp.status_code} for {season}",
                flush=True,
            )
            _team_batting_cache[season] = data
            return data

        rows = _parse_csv(resp.text)
        cols = list(rows[0].keys()) if rows else []
        print(
            f"[statcast] Team batting CSV: {len(rows)} rows | cols: {cols}",
            flush=True,
        )
        if rows:
            print(f"[statcast] Team batting sample row: {rows[0]}", flush=True)

        # Try every plausible team-id column name Baseball Savant might use
        _TID_KEYS = ("team_id", "teamid", "player_team_id", "team", "org_id", "org")

        from collections import defaultdict
        buckets: dict = defaultdict(lambda: {"ev": [], "br": [], "hh": []})
        for row in rows:
            tid = next((row[k] for k in _TID_KEYS if row.get(k)), None)
            if not tid:
                continue
            ev = _safe_float(row.get("exit_velocity_avg") or row.get("avg_hit_speed"))
            br = _safe_float(row.get("barrel_batted_rate") or row.get("barrel_rate"))
            hh = _safe_float(row.get("hard_hit_percent")  or row.get("hard_hit_rate"))
            b = buckets[str(tid)]
            if ev is not None: b["ev"].append(ev)
            if br is not None: b["br"].append(br)
            if hh is not None: b["hh"].append(hh)

        for tid, vals in buckets.items():
            data[tid] = {
                "exit_velocity_avg": sum(vals["ev"]) / len(vals["ev"]) if vals["ev"] else None,
                "barrel_rate":       sum(vals["br"]) / len(vals["br"]) if vals["br"] else None,
                "hard_hit_rate":     sum(vals["hh"]) / len(vals["hh"]) if vals["hh"] else None,
            }

        print(
            f"[statcast] Team batting: {len(data)} teams aggregated for {season}"
            f" (tid keys tried: {_TID_KEYS})",
            flush=True,
        )
        if len(data) == 0 and rows:
            print(
                "[statcast] WARNING: 0 teams aggregated but rows exist — "
                f"none of {_TID_KEYS} found in columns {cols}",
                flush=True,
            )
        _save_to_disk("team_batting", season, data)

    except Exception as e:
        print(f"[statcast] Team batting fetch failed ({season}): {e}", flush=True)

    _team_batting_cache[season] = data
    return data


# ── Sprint speed ──────────────────────────────────────────────────────────────

def _load_sprint_speed_season(season: int) -> dict:
    """
    Download sprint speed leaderboard for a full season, aggregate to team level.
    Returns {str(team_id): {"sprint_speed_avg": float}}.
    One HTTP request per season; all teams populated at once.
    """
    if season in _team_sprint_cache:
        return _team_sprint_cache[season]

    disk_data = _load_from_disk("sprint_speed", season)
    if disk_data is not None:
        _team_sprint_cache[season] = disk_data
        print(
            f"[statcast] Sprint speed {season}: loaded {len(disk_data)} teams from disk",
            flush=True,
        )
        return disk_data

    data: dict = {}
    try:
        time.sleep(_REQUEST_DELAY)
        # No team filter — fetch all players, aggregate per team_id
        url = (
            "https://baseballsavant.mlb.com/leaderboard/sprint_speed"
            f"?min_opp=0&position=&team=&year={season}&csv=true"
        )
        print(f"[statcast] Fetching sprint speed leaderboard: {url}", flush=True)
        resp = requests.get(url, timeout=_TIMEOUT, headers=_SAVANT_HEADERS)

        if resp.status_code != 200:
            print(
                f"[statcast] Sprint speed HTTP {resp.status_code} for {season}",
                flush=True,
            )
            _team_sprint_cache[season] = data
            return data

        rows = _parse_csv(resp.text)
        print(
            f"[statcast] Sprint speed CSV rows: {len(rows)}"
            f" | cols: {list(rows[0].keys()) if rows else 'none'}",
            flush=True,
        )

        from collections import defaultdict
        buckets: dict = defaultdict(list)
        for row in rows:
            tid = row.get("team_id") or row.get("teamid")
            speed = _safe_float(row.get("sprint_speed") or row.get("hp_to_1b"))
            if tid and speed is not None:
                buckets[str(tid)].append(speed)

        for tid, speeds in buckets.items():
            if speeds:
                data[tid] = {"sprint_speed_avg": sum(speeds) / len(speeds)}

        print(
            f"[statcast] Sprint speed: {len(data)} teams aggregated for {season}",
            flush=True,
        )
        _save_to_disk("sprint_speed", season, data)

    except Exception as e:
        print(f"[statcast] Sprint speed fetch failed ({season}): {e}", flush=True)

    _team_sprint_cache[season] = data
    return data


def fetch_team_statcast(team_id: int, season: int) -> dict | None:
    """
    Return team-level Statcast metrics: exit_velocity_avg, barrel_rate,
    hard_hit_rate, sprint_speed_avg. Falls back to prior season. Returns None
    if all sources are unavailable.
    """
    try:
        for s in (season, season - 1):
            batting = _load_team_batting_season(s).get(str(team_id))
            sprint  = _load_sprint_speed_season(s).get(str(team_id))
            if batting or sprint:
                return {
                    "exit_velocity_avg": batting.get("exit_velocity_avg") if batting else None,
                    "barrel_rate":       batting.get("barrel_rate")       if batting else None,
                    "hard_hit_rate":     batting.get("hard_hit_rate")     if batting else None,
                    "sprint_speed_avg":  sprint.get("sprint_speed_avg")   if sprint  else None,
                }
        return None
    except Exception as e:
        print(f"[statcast] fetch_team_statcast({team_id}, {season}): {e}", flush=True)
        return None
