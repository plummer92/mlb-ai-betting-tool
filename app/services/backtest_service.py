"""
Point-in-time backtest service.

Historical rows are built chronologically from prior completed games only.
No season-end team or pitcher aggregates are reused across earlier games.
"""
import json
import logging
import math
import time
import traceback
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone

import numpy as np
import requests
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss as sk_log_loss
from sklearn.preprocessing import StandardScaler

from app.config import MLB_API_BASE
from app.models.schema import BacktestGame, BacktestResult, GameOdds, GameOutcomeReview, SandboxPredictionV4, SnapshotType
from app.services.feature_builder import PARK_FACTORS, PYTHAG_EXPONENT
from app.services.mlb_api import fetch_pitcher_stats

logger = logging.getLogger(__name__)

_COMMIT_INTERVAL = 50
_DEFAULT_KBB = 5.3
_DEFAULT_WHIP = 1.30
_DEFAULT_OPS = 0.720
_DEFAULT_PYTHAG = 0.500
_DEFAULT_ODDS_POLICY = "closest_prior"

FEATURE_NAMES = [
    "pythagorean_win_pct_adv",
    "kbb_adv",
    "park_factor_adv",
    "run_diff_adv",
]

POINT_IN_TIME_WARNING = (
    "Backtest rows are rebuilt with a strict game-start cutoff. Rows with missing "
    "pregame features or missing historical odds linkage are flagged explicitly."
)


@dataclass
class TeamRollingStats:
    games_played: int = 0
    wins: int = 0
    losses: int = 0
    runs_scored: int = 0
    runs_allowed: int = 0
    batting_ab: int = 0
    batting_hits: int = 0
    batting_doubles: int = 0
    batting_triples: int = 0
    batting_home_runs: int = 0
    batting_walks: int = 0
    batting_hbp: int = 0
    batting_sf: int = 0
    pitching_outs: int = 0
    pitching_er: int = 0
    pitching_hits_allowed: int = 0
    pitching_walks: int = 0
    bullpen_outs: int = 0
    bullpen_er: int = 0


@dataclass
class PitcherRollingStats:
    starts: int = 0
    outs: int = 0
    earned_runs: int = 0
    hits_allowed: int = 0
    walks: int = 0
    strikeouts: int = 0


def build_live_feature_vector(home_team: dict, away_team: dict) -> dict[str, float]:
    return {
        "home_whip_adv": float(away_team.get("team_whip", _DEFAULT_WHIP)) - float(home_team.get("team_whip", _DEFAULT_WHIP)),
        "home_ops_adv": float(home_team.get("ops", _DEFAULT_OPS)) - float(away_team.get("ops", _DEFAULT_OPS)),
        "run_diff_adv": float(home_team.get("run_differential_per_game") or 0.0) - float(away_team.get("run_differential_per_game") or 0.0),
        "kbb_adv": float(home_team.get("starter_kbb_percent") or 0.12) - float(away_team.get("starter_kbb_percent") or 0.12),
        "park_factor_adv": float(home_team.get("park_run_factor", 1.0)) - 1.0,
        "pythagorean_win_pct_adv": float(home_team.get("pythagorean_win_pct") or _DEFAULT_PYTHAG) - float(away_team.get("pythagorean_win_pct") or _DEFAULT_PYTHAG),
    }


def score_logistic_home_probability(features: dict[str, float], result: BacktestResult | None) -> float | None:
    if result is None:
        return None

    try:
        coefs = json.loads(result.coefficients_json)
        intercept = float(coefs.get("__intercept__"))
        means = coefs.get("__scaler_mean__")
        scales = coefs.get("__scaler_scale__")
        if not isinstance(means, list) or not isinstance(scales, list):
            return None

        logit = intercept
        for idx, feature_name in enumerate(FEATURE_NAMES):
            raw_value = float(features.get(feature_name, 0.0))
            mean = float(means[idx])
            scale = float(scales[idx] or 1.0)
            standardized = (raw_value - mean) / scale if scale else 0.0
            logit += float(coefs.get(feature_name, 0.0)) * standardized
        probability = 1.0 / (1.0 + math.exp(-logit))
        return round(max(0.05, min(0.95, probability)), 4)
    except Exception:
        return None


def _safe_float(value, default: float | None = None) -> float | None:
    if value in (None, "", "-", "--", "---", "-.--", ".---"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 0) -> int:
    if value in (None, "", "-", "--", "---"):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_innings_to_outs(value: str | None) -> int:
    if not value:
        return 0
    try:
        whole, _, fraction = str(value).partition(".")
        outs = int(whole) * 3
        if fraction:
            outs += int(fraction[:1])
        return outs
    except (TypeError, ValueError):
        return 0


def _outs_to_ip(outs: int) -> float:
    return outs / 3.0 if outs > 0 else 0.0


def _calc_ops(stats: TeamRollingStats) -> float | None:
    if stats.batting_ab <= 0:
        return None
    singles = max(stats.batting_hits - stats.batting_doubles - stats.batting_triples - stats.batting_home_runs, 0)
    total_bases = singles + (2 * stats.batting_doubles) + (3 * stats.batting_triples) + (4 * stats.batting_home_runs)
    obp_denom = stats.batting_ab + stats.batting_walks + stats.batting_hbp + stats.batting_sf
    obp = ((stats.batting_hits + stats.batting_walks + stats.batting_hbp) / obp_denom) if obp_denom > 0 else None
    slg = (total_bases / stats.batting_ab) if stats.batting_ab > 0 else None
    if obp is None or slg is None:
        return None
    return round(obp + slg, 4)


def _calc_team_whip(stats: TeamRollingStats) -> float | None:
    ip = _outs_to_ip(stats.pitching_outs)
    if ip <= 0:
        return None
    return round((stats.pitching_hits_allowed + stats.pitching_walks) / ip, 4)


def _calc_team_era(stats: TeamRollingStats) -> float | None:
    if stats.pitching_outs <= 0:
        return None
    return round((stats.pitching_er * 27.0) / stats.pitching_outs, 4)


def _calc_bullpen_era(stats: TeamRollingStats) -> float | None:
    if stats.bullpen_outs <= 0:
        return None
    return round((stats.bullpen_er * 27.0) / stats.bullpen_outs, 4)


def _calc_pythagorean_win_pct(runs_scored: int, runs_allowed: int) -> float | None:
    if runs_scored <= 0 or runs_allowed <= 0:
        return None
    scored = max(float(runs_scored), 1.0)
    allowed = max(float(runs_allowed), 1.0)
    return round((scored ** PYTHAG_EXPONENT) / ((scored ** PYTHAG_EXPONENT) + (allowed ** PYTHAG_EXPONENT)), 4)


def _team_snapshot(stats: TeamRollingStats | None) -> dict:
    if stats is None or stats.games_played <= 0:
        return {
            "games_played": 0,
            "win_pct": None,
            "ops": None,
            "team_whip": None,
            "team_era": None,
            "run_diff": None,
            "run_diff_per_game": None,
            "pythagorean_win_pct": None,
            "bullpen_era": None,
        }
    return {
        "games_played": stats.games_played,
        "win_pct": round(stats.wins / stats.games_played, 4),
        "ops": _calc_ops(stats),
        "team_whip": _calc_team_whip(stats),
        "team_era": _calc_team_era(stats),
        "run_diff": stats.runs_scored - stats.runs_allowed,
        "run_diff_per_game": round((stats.runs_scored - stats.runs_allowed) / stats.games_played, 4),
        "pythagorean_win_pct": _calc_pythagorean_win_pct(stats.runs_scored, stats.runs_allowed),
        "bullpen_era": _calc_bullpen_era(stats),
    }


def _pitcher_snapshot(stats: PitcherRollingStats | None) -> dict:
    if stats is None or stats.starts <= 0 or stats.outs <= 0:
        return {
            "starts": 0,
            "era": None,
            "whip": None,
            "kbb": None,
            "kbb_percent": None,
        }
    ip = _outs_to_ip(stats.outs)
    k9 = (stats.strikeouts * 9.0 / ip) if ip > 0 else None
    bb9 = (stats.walks * 9.0 / ip) if ip > 0 else None
    kbb = (k9 - bb9) if k9 is not None and bb9 is not None else None
    return {
        "starts": stats.starts,
        "era": round((stats.earned_runs * 27.0) / stats.outs, 4) if stats.outs > 0 else None,
        "whip": round((stats.hits_allowed + stats.walks) / ip, 4) if ip > 0 else None,
        "kbb": round(kbb, 4) if kbb is not None else None,
        "kbb_percent": round(max(0.05, min(0.25, kbb / 45.0)), 4) if kbb is not None else None,
    }


def _get_with_retry(url: str, max_attempts: int = 3, base_backoff: float = 3.0) -> requests.Response:
    for attempt in range(max_attempts):
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 429:
                wait = base_backoff * (2 ** attempt)
                print(f"[backtest] Rate limited ({url.split('?')[0]}) — sleeping {wait:.1f}s before retry {attempt+1}/{max_attempts}", flush=True)
                time.sleep(wait)
                continue
            if resp.status_code >= 500 and attempt < max_attempts - 1:
                wait = base_backoff * (2 ** attempt)
                print(f"[backtest] Server error {resp.status_code} ({url.split('?')[0]}) — sleeping {wait:.1f}s", flush=True)
                time.sleep(wait)
                continue
            return resp
        except requests.exceptions.Timeout:
            if attempt == max_attempts - 1:
                raise
            wait = base_backoff * (2 ** attempt)
            print(f"[backtest] Timeout on {url.split('?')[0]} (attempt {attempt+1}/{max_attempts}) — retrying in {wait:.1f}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"All {max_attempts} attempts failed: {url}")


def _fetch_season_schedule(season: int) -> list[dict]:
    url = (
        f"{MLB_API_BASE}/schedule"
        f"?sportId=1&season={season}&gameType=R"
        f"&startDate={season}-03-01&endDate={season}-11-30"
        f"&hydrate=team,linescore,probablePitcher,venue"
    )
    resp = _get_with_retry(url)
    resp.raise_for_status()
    payload = resp.json()

    games = []
    for day in payload.get("dates", []):
        for g in day.get("games", []):
            status = g.get("status", {}).get("abstractGameState", "")
            if status != "Final":
                continue
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            away_pitcher = away.get("probablePitcher") or {}
            home_pitcher = home.get("probablePitcher") or {}
            games.append({
                "game_id": g["gamePk"],
                "game_date": day["date"],
                "season": season,
                "away_team": away["team"]["name"],
                "away_team_id": away["team"]["id"],
                "home_team": home["team"]["name"],
                "home_team_id": home["team"]["id"],
                "venue": g.get("venue", {}).get("name"),
                "away_score": away.get("score"),
                "home_score": home.get("score"),
                "home_win": (home.get("score", 0) or 0) > (away.get("score", 0) or 0),
                "game_start_time": g.get("gameDate"),
                "away_starter_id": away_pitcher.get("id"),
                "home_starter_id": home_pitcher.get("id"),
                "away_starter_name": away_pitcher.get("fullName"),
                "home_starter_name": home_pitcher.get("fullName"),
            })
    return games


def _fetch_game_feed(game_id: int) -> dict | None:
    try:
        resp = _get_with_retry(f"{MLB_API_BASE}/game/{game_id}/feed/live")
    except Exception:
        raise
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _extract_player_stat(players: dict, player_id: int | None, group: str) -> dict:
    if not player_id:
        return {}
    player = players.get(f"ID{player_id}") or {}
    return ((player.get("stats") or {}).get(group) or {})


def _extract_team_boxscore(game: dict, side: str) -> dict:
    team = (((game.get("liveData") or {}).get("boxscore") or {}).get("teams") or {}).get(side) or {}
    players = team.get("players") or {}
    pitcher_ids = team.get("pitchers") or []
    actual_starter_id = pitcher_ids[0] if pitcher_ids else None
    starter_pitching = _extract_player_stat(players, actual_starter_id, "pitching")

    bullpen_er = 0
    bullpen_outs = 0
    for pitcher_id in pitcher_ids[1:]:
        stat = _extract_player_stat(players, pitcher_id, "pitching")
        bullpen_er += _safe_int(stat.get("earnedRuns"), 0)
        bullpen_outs += _parse_innings_to_outs(stat.get("inningsPitched"))

    team_batting = (team.get("teamStats") or {}).get("batting") or {}
    team_pitching = (team.get("teamStats") or {}).get("pitching") or {}
    starter_name = ((players.get(f"ID{actual_starter_id}") or {}).get("person") or {}).get("fullName") if actual_starter_id else None
    return {
        "starter_id": actual_starter_id,
        "starter_name": starter_name,
        "starter_outs": _parse_innings_to_outs(starter_pitching.get("inningsPitched")),
        "starter_er": _safe_int(starter_pitching.get("earnedRuns"), 0),
        "starter_hits_allowed": _safe_int(starter_pitching.get("hits"), 0),
        "starter_walks": _safe_int(starter_pitching.get("baseOnBalls"), 0),
        "starter_strikeouts": _safe_int(starter_pitching.get("strikeOuts"), 0),
        "team_ab": _safe_int(team_batting.get("atBats"), 0),
        "team_hits": _safe_int(team_batting.get("hits"), 0),
        "team_doubles": _safe_int(team_batting.get("doubles"), 0),
        "team_triples": _safe_int(team_batting.get("triples"), 0),
        "team_home_runs": _safe_int(team_batting.get("homeRuns"), 0),
        "team_walks": _safe_int(team_batting.get("baseOnBalls"), 0),
        "team_hbp": _safe_int(team_batting.get("hitByPitch"), 0),
        "team_sf": _safe_int(team_batting.get("sacFlies"), 0),
        "team_pitching_outs": _parse_innings_to_outs(team_pitching.get("inningsPitched")),
        "team_pitching_er": _safe_int(team_pitching.get("earnedRuns"), 0),
        "team_pitching_hits_allowed": _safe_int(team_pitching.get("hits"), 0),
        "team_pitching_walks": _safe_int(team_pitching.get("baseOnBalls"), 0),
        "bullpen_er": bullpen_er,
        "bullpen_outs": bullpen_outs,
    }


def _extract_game_fact(schedule_game: dict) -> dict | None:
    feed = _fetch_game_feed(schedule_game["game_id"])
    if feed is None:
        print(f"[backtest] Game {schedule_game['game_id']} missing feed — using schedule-only data")
        away_box = {}
        home_box = {}
    else:
        away_box = _extract_team_boxscore(feed, "away")
        home_box = _extract_team_boxscore(feed, "home")
    start_time = _parse_iso_datetime(schedule_game.get("game_start_time"))
    if start_time is None:
        start_time = _parse_iso_datetime((((feed or {}).get("gameData") or {}).get("datetime") or {}).get("dateTime"))
    if start_time is None:
        start_time = datetime.combine(date.fromisoformat(schedule_game["game_date"]), datetime.min.time(), tzinfo=timezone.utc)

    return {
        **schedule_game,
        "game_start_dt": start_time,
        "away_starter_id": away_box.get("starter_id") or schedule_game.get("away_starter_id"),
        "home_starter_id": home_box.get("starter_id") or schedule_game.get("home_starter_id"),
        "away_starter_name": away_box.get("starter_name") or schedule_game.get("away_starter_name"),
        "home_starter_name": home_box.get("starter_name") or schedule_game.get("home_starter_name"),
        "away_box": away_box,
        "home_box": home_box,
    }


def _update_team_state(stats: TeamRollingStats, box: dict, runs_scored: int, runs_allowed: int, won: bool) -> None:
    stats.games_played += 1
    stats.wins += 1 if won else 0
    stats.losses += 0 if won else 1
    stats.runs_scored += _safe_int(runs_scored, 0)
    stats.runs_allowed += _safe_int(runs_allowed, 0)
    stats.batting_ab += _safe_int(box.get("team_ab"), 0)
    stats.batting_hits += _safe_int(box.get("team_hits"), 0)
    stats.batting_doubles += _safe_int(box.get("team_doubles"), 0)
    stats.batting_triples += _safe_int(box.get("team_triples"), 0)
    stats.batting_home_runs += _safe_int(box.get("team_home_runs"), 0)
    stats.batting_walks += _safe_int(box.get("team_walks"), 0)
    stats.batting_hbp += _safe_int(box.get("team_hbp"), 0)
    stats.batting_sf += _safe_int(box.get("team_sf"), 0)
    stats.pitching_outs += _safe_int(box.get("team_pitching_outs"), 0)
    stats.pitching_er += _safe_int(box.get("team_pitching_er"), 0)
    stats.pitching_hits_allowed += _safe_int(box.get("team_pitching_hits_allowed"), 0)
    stats.pitching_walks += _safe_int(box.get("team_pitching_walks"), 0)
    stats.bullpen_outs += _safe_int(box.get("bullpen_outs"), 0)
    stats.bullpen_er += _safe_int(box.get("bullpen_er"), 0)


def _update_pitcher_state(stats: PitcherRollingStats, box: dict) -> None:
    outs = _safe_int(box.get("starter_outs"), 0)
    if outs <= 0:
        return
    stats.starts += 1
    stats.outs += outs
    stats.earned_runs += _safe_int(box.get("starter_er"), 0)
    stats.hits_allowed += _safe_int(box.get("starter_hits_allowed"), 0)
    stats.walks += _safe_int(box.get("starter_walks"), 0)
    stats.strikeouts += _safe_int(box.get("starter_strikeouts"), 0)


def _select_historical_odds_snapshot(
    db: Session,
    *,
    game_id: int,
    cutoff_time: datetime,
    policy: str = _DEFAULT_ODDS_POLICY,
) -> GameOdds | None:
    base_query = (
        db.query(GameOdds)
        .filter(GameOdds.game_id == game_id, GameOdds.fetched_at <= cutoff_time)
        .order_by(GameOdds.fetched_at.desc(), GameOdds.id.desc())
    )
    if policy == "pregame":
        return base_query.filter(GameOdds.snapshot_type == SnapshotType.pregame).first()
    if policy == "open":
        return base_query.filter(GameOdds.snapshot_type == SnapshotType.open).first()
    if policy == "closest_prior":
        rows = base_query.filter(GameOdds.snapshot_type.in_([SnapshotType.pregame, SnapshotType.open])).limit(10).all()
        if not rows:
            return None
        rows.sort(
            key=lambda row: (
                row.fetched_at or datetime.min.replace(tzinfo=timezone.utc),
                1 if row.snapshot_type == SnapshotType.pregame else 0,
                row.id or 0,
            ),
            reverse=True,
        )
        return rows[0]
    raise ValueError(f"Unsupported odds policy: {policy}")


def _season_needs_stale_pit_rebuild(db: Session, season: int) -> tuple[int, int]:
    existing_rows = (
        db.query(BacktestGame.game_id, BacktestGame.feature_cutoff_time, BacktestGame.odds_snapshot_policy, BacktestGame.incomplete_reasons_json)
        .filter(BacktestGame.season == season)
        .all()
    )
    stale_rows = [
        row for row in existing_rows
        if row.feature_cutoff_time is None
        and row.odds_snapshot_policy is None
        and row.incomplete_reasons_json is None
    ]
    return len(existing_rows), len(stale_rows)


def collect_season(db: Session, season: int, *, odds_policy: str = _DEFAULT_ODDS_POLICY) -> int:
    print(f"[backtest] Starting collection for season {season}", flush=True)
    existing_rows_before, stale_rows_before = _season_needs_stale_pit_rebuild(db, season)
    if stale_rows_before:
        deleted = (
            db.query(BacktestGame)
            .filter(BacktestGame.season == season)
            .delete(synchronize_session=False)
        )
        db.commit()
        print(
            f"[backtest] Season {season}: deleted {deleted} stale pre-PIT rows before rebuild "
            f"(existing={existing_rows_before}, stale={stale_rows_before})",
            flush=True,
        )
        existing_game_ids: set[int] = set()
    else:
        existing_game_ids = {
            game_id for (game_id,) in db.query(BacktestGame.game_id).filter(BacktestGame.season == season).all()
        }

    schedule_games = _fetch_season_schedule(season)
    print(f"[backtest] Season {season}: {len(schedule_games)} completed games found in schedule", flush=True)
    if not schedule_games:
        return 0

    facts: list[dict] = []
    skip_reason_counts: Counter[str] = Counter()
    for idx, game in enumerate(schedule_games, start=1):
        try:
            game_id = game["game_id"]
            fact = _extract_game_fact(game)
            if fact is None:
                print(f"[backtest] Skipping game {game_id} — no feed available", flush=True)
                continue
            facts.append(fact)
            if idx % _COMMIT_INTERVAL == 0:
                print(f"[backtest] Season {season}: fetched {idx}/{len(schedule_games)} game feeds", flush=True)
        except Exception:
            skip_reason_counts.update(["feed_fetch_error"])
            print(f"[backtest] Season {season} — feed error on game {game['game_id']} ({idx}/{len(schedule_games)}):", flush=True)
            traceback.print_exc()

    facts.sort(key=lambda item: (item["game_start_dt"], item["game_id"]))
    team_state: dict[int, TeamRollingStats] = {}
    pitcher_state: dict[int, PitcherRollingStats] = {}
    pitcher_api_cache: dict[int, dict] = {}
    processed = 0
    errors = 0
    inserted = 0
    updated = 0
    features_complete_count = 0
    odds_complete_count = 0

    for idx, fact in enumerate(facts, start=1):
        try:
            home_team_pre = _team_snapshot(team_state.get(fact["home_team_id"]))
            away_team_pre = _team_snapshot(team_state.get(fact["away_team_id"]))
            
            home_starter_id = fact.get("home_starter_id")
            away_starter_id = fact.get("away_starter_id")
            home_pitcher_pre = _pitcher_snapshot(pitcher_state.get(home_starter_id))
            away_pitcher_pre = _pitcher_snapshot(pitcher_state.get(away_starter_id))
            
            # Fallback to season-level kbb from MLB API if rolling data is insufficient.
            # This ensures we have a baseline for first-time starters in the backtest.
            if home_pitcher_pre["kbb"] is None and home_starter_id:
                if home_starter_id not in pitcher_api_cache:
                    pitcher_api_cache[home_starter_id] = fetch_pitcher_stats(home_starter_id, season)
                api_stats = pitcher_api_cache[home_starter_id]
                if api_stats:
                    home_pitcher_pre["kbb"] = api_stats.get("kbb")
                    if home_pitcher_pre["era"] is None: home_pitcher_pre["era"] = api_stats.get("era")
                    if home_pitcher_pre["whip"] is None: home_pitcher_pre["whip"] = api_stats.get("whip")

            if away_pitcher_pre["kbb"] is None and away_starter_id:
                if away_starter_id not in pitcher_api_cache:
                    pitcher_api_cache[away_starter_id] = fetch_pitcher_stats(away_starter_id, season)
                api_stats = pitcher_api_cache[away_starter_id]
                if api_stats:
                    away_pitcher_pre["kbb"] = api_stats.get("kbb")
                    if away_pitcher_pre["era"] is None: away_pitcher_pre["era"] = api_stats.get("era")
                    if away_pitcher_pre["whip"] is None: away_pitcher_pre["whip"] = api_stats.get("whip")

            cutoff_time = fact["game_start_dt"]
            odds_row = _select_historical_odds_snapshot(
                db,
                game_id=fact["game_id"],
                cutoff_time=cutoff_time,
                policy=odds_policy,
            )

            incomplete_reasons: list[str] = []
            if home_team_pre["games_played"] <= 0:
                incomplete_reasons.append("missing_home_prior_team_games")
            if away_team_pre["games_played"] <= 0:
                incomplete_reasons.append("missing_away_prior_team_games")
            
            if home_starter_id is None:
                incomplete_reasons.append("missing_home_starter_id")
            elif home_pitcher_pre["starts"] <= 0 and home_pitcher_pre["kbb"] is None:
                incomplete_reasons.append("missing_home_prior_starter_data")
                
            if away_starter_id is None:
                incomplete_reasons.append("missing_away_starter_id")
            elif away_pitcher_pre["starts"] <= 0 and away_pitcher_pre["kbb"] is None:
                incomplete_reasons.append("missing_away_prior_starter_data")
            
            if odds_row is None:
                incomplete_reasons.append(f"missing_odds_snapshot:{odds_policy}")

            features_complete = not any(reason.startswith("missing_home_prior") or reason.startswith("missing_away_prior") or reason.endswith("starter_id") or reason.endswith("starter_data") for reason in incomplete_reasons)
            odds_complete = odds_row is not None

            row_data = dict(
                game_id=fact["game_id"],
                game_date=date.fromisoformat(fact["game_date"]),
                season=season,
                home_team_id=fact["home_team_id"],
                away_team_id=fact["away_team_id"],
                home_team=fact["home_team"],
                away_team=fact["away_team"],
                venue=fact["venue"],
                game_start_time=cutoff_time,
                feature_cutoff_time=cutoff_time,
                feature_cutoff_policy="game_start",
                home_score=fact["home_score"],
                away_score=fact["away_score"],
                home_win=fact["home_win"],
                home_starter_id=fact.get("home_starter_id"),
                away_starter_id=fact.get("away_starter_id"),
                home_starter_name=fact.get("home_starter_name"),
                away_starter_name=fact.get("away_starter_name"),
                home_starter_era=home_pitcher_pre["era"],
                away_starter_era=away_pitcher_pre["era"],
                home_starter_kbb=home_pitcher_pre["kbb"],
                away_starter_kbb=away_pitcher_pre["kbb"],
                home_starter_whip=home_pitcher_pre["whip"],
                away_starter_whip=away_pitcher_pre["whip"],
                home_starter_starts=home_pitcher_pre["starts"],
                away_starter_starts=away_pitcher_pre["starts"],
                home_team_era=home_team_pre["team_era"],
                away_team_era=away_team_pre["team_era"],
                home_team_ops=home_team_pre["ops"],
                away_team_ops=away_team_pre["ops"],
                home_team_whip=home_team_pre["team_whip"],
                away_team_whip=away_team_pre["team_whip"],
                home_win_pct=home_team_pre["win_pct"],
                away_win_pct=away_team_pre["win_pct"],
                home_games_played=home_team_pre["games_played"],
                away_games_played=away_team_pre["games_played"],
                home_run_diff=home_team_pre["run_diff"],
                away_run_diff=away_team_pre["run_diff"],
                home_pythagorean_win_pct=home_team_pre["pythagorean_win_pct"],
                away_pythagorean_win_pct=away_team_pre["pythagorean_win_pct"],
                home_bullpen_era=home_team_pre["bullpen_era"],
                away_bullpen_era=away_team_pre["bullpen_era"],
                home_exit_velo=None,
                away_exit_velo=None,
                home_barrel_rate=None,
                away_barrel_rate=None,
                home_hard_hit_rate=None,
                away_hard_hit_rate=None,
                home_sprint_speed=None,
                away_sprint_speed=None,
                odds_snapshot_type=odds_row.snapshot_type.value if odds_row else None,
                odds_snapshot_policy=odds_policy,
                odds_row_id=odds_row.id if odds_row else None,
                odds_fetched_at=odds_row.fetched_at if odds_row else None,
                odds_away_ml=odds_row.away_ml if odds_row else None,
                odds_home_ml=odds_row.home_ml if odds_row else None,
                odds_total=odds_row.total_line if odds_row else None,
                features_complete=features_complete,
                odds_complete=odds_complete,
                incomplete_reasons_json=json.dumps(incomplete_reasons),
            )

            stmt = pg_insert(BacktestGame).values(**row_data)
            stmt = stmt.on_conflict_do_update(
                index_elements=["game_id"],
                set_={k: v for k, v in row_data.items() if k != "game_id"},
            )
            print(f"[backtest] inserting game {fact['game_id']}", flush=True)
            db.execute(stmt)
            db.commit()
            processed += 1
            if fact["game_id"] in existing_game_ids:
                updated += 1
            else:
                inserted += 1
                existing_game_ids.add(fact["game_id"])
            if features_complete:
                features_complete_count += 1
            if odds_complete:
                odds_complete_count += 1
            skip_reason_counts.update(incomplete_reasons)

            team_state.setdefault(fact["home_team_id"], TeamRollingStats())
            team_state.setdefault(fact["away_team_id"], TeamRollingStats())
            _update_team_state(
                team_state[fact["home_team_id"]],
                fact["home_box"],
                fact["home_score"],
                fact["away_score"],
                bool(fact["home_win"]),
            )
            _update_team_state(
                team_state[fact["away_team_id"]],
                fact["away_box"],
                fact["away_score"],
                fact["home_score"],
                not bool(fact["home_win"]),
            )

            if fact.get("home_starter_id") is not None:
                pitcher_state.setdefault(fact["home_starter_id"], PitcherRollingStats())
                _update_pitcher_state(pitcher_state[fact["home_starter_id"]], fact["home_box"])
            if fact.get("away_starter_id") is not None:
                pitcher_state.setdefault(fact["away_starter_id"], PitcherRollingStats())
                _update_pitcher_state(pitcher_state[fact["away_starter_id"]], fact["away_box"])

            if processed % 10 == 0:
                db.commit()
                print(f"[backtest] committed {processed} rows so far", flush=True)
        except Exception:
            errors += 1
            skip_reason_counts.update(["row_build_or_upsert_error"])
            print(f"[backtest] Season {season} — ERROR on game {fact['game_id']} ({idx}/{len(facts)}):", flush=True)
            traceback.print_exc()
            db.rollback()

    db.commit()
    print(f"[backtest] Season {season} COMPLETE: {processed}/{len(facts)} games stored, {errors} errors", flush=True)
    print(
        (
            f"[backtest] Season {season} diagnostics: rows_scanned={len(schedule_games)} "
            f"rows_inserted={inserted} rows_updated={updated} "
            f"rows_marked_features_complete={features_complete_count} "
            f"rows_marked_odds_complete={odds_complete_count}"
        ),
        flush=True,
    )
    print(
        f"[backtest] Season {season} skip reasons: {json.dumps(dict(sorted(skip_reason_counts.items())), sort_keys=True)}",
        flush=True,
    )
    return processed


def _row_to_feature_vector(row: BacktestGame) -> list[float]:
    run_diff_home = (row.home_run_diff / row.home_games_played) if row.home_run_diff is not None and row.home_games_played else 0.0
    run_diff_away = (row.away_run_diff / row.away_games_played) if row.away_run_diff is not None and row.away_games_played else 0.0
    home_kbb_pct = ((row.home_starter_kbb if row.home_starter_kbb is not None else _DEFAULT_KBB) / 45.0)
    away_kbb_pct = ((row.away_starter_kbb if row.away_starter_kbb is not None else _DEFAULT_KBB) / 45.0)
    return [
        (row.home_pythagorean_win_pct or _DEFAULT_PYTHAG) - (row.away_pythagorean_win_pct or _DEFAULT_PYTHAG),
        home_kbb_pct - away_kbb_pct,
        PARK_FACTORS.get(row.venue or "", 0.0),
        run_diff_home - run_diff_away,
    ]


def _fit_logistic_model(rows: list[BacktestGame]) -> tuple[StandardScaler, LogisticRegression]:
    X = np.array([_row_to_feature_vector(row) for row in rows], dtype=float)
    y = np.array([int(row.home_win) for row in rows], dtype=int)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    model = LogisticRegression(max_iter=1000, random_state=42)
    model.fit(X_scaled, y)
    return scaler, model


def _score_rows(rows: list[BacktestGame], scaler: StandardScaler, model: LogisticRegression) -> list[float]:
    X = np.array([_row_to_feature_vector(row) for row in rows], dtype=float)
    return model.predict_proba(scaler.transform(X))[:, 1].tolist()


def _fold_metrics(y_true: list[int], probs: list[float]) -> dict:
    preds = [1 if p >= 0.5 else 0 for p in probs]
    accuracy = float(sum(int(a == b) for a, b in zip(preds, y_true)) / len(y_true))
    return {
        "accuracy": round(accuracy, 4),
        "log_loss": round(float(sk_log_loss(y_true, probs)), 4),
        "brier_score": round(float(brier_score_loss(y_true, probs)), 4),
    }


def run_logistic_regression(db: Session, seasons: list[int], apply_weights: bool = True) -> BacktestResult:
    rows = (
        db.query(BacktestGame)
        .filter(
            BacktestGame.season.in_(seasons),
            BacktestGame.home_win.isnot(None),
        )
        .order_by(BacktestGame.feature_cutoff_time.asc(), BacktestGame.game_id.asc())
        .all()
    )
    if len(rows) < 50:
        raise ValueError(f"Too few backtest rows ({len(rows)}). Run collect first.")

    clean_rows = [row for row in rows if row.features_complete]
    if len(clean_rows) < 50:
        raise ValueError(f"Too few point-in-time-complete rows ({len(clean_rows)}).")

    final_scaler, final_model = _fit_logistic_model(clean_rows)
    unique_seasons = sorted({row.season for row in clean_rows})
    validation_mode = "walk_forward_by_season"
    folds: list[dict] = []
    oos_predictions: list[dict] = []

    if len(unique_seasons) >= 2:
        for eval_season in unique_seasons[1:]:
            train_rows = [row for row in clean_rows if row.season < eval_season]
            test_rows = [row for row in clean_rows if row.season == eval_season]
            if len(train_rows) < 50 or not test_rows:
                continue
            scaler, model = _fit_logistic_model(train_rows)
            probs = _score_rows(test_rows, scaler, model)
            y_true = [int(row.home_win) for row in test_rows]
            metrics = _fold_metrics(y_true, probs)
            folds.append({
                "eval_season": eval_season,
                "train_rows": len(train_rows),
                "test_rows": len(test_rows),
                **metrics,
            })
            for row, prob, actual in zip(test_rows, probs, y_true):
                oos_predictions.append({
                    "game_id": row.game_id,
                    "season": row.season,
                    "cutoff_time": row.feature_cutoff_time.isoformat() if row.feature_cutoff_time else None,
                    "raw_prob": float(prob),
                    "actual": int(actual),
                    "odds_complete": bool(row.odds_complete),
                })

        if not oos_predictions:
            raise ValueError("Walk-forward validation produced no out-of-sample folds.")

        pooled_y = [row["actual"] for row in oos_predictions]
        pooled_probs = [row["raw_prob"] for row in oos_predictions]
        pooled_metrics = _fold_metrics(pooled_y, pooled_probs)
        cv_accuracy = round(float(sum(fold["accuracy"] for fold in folds) / len(folds)), 4)
    else:
        validation_mode = "single_season_in_sample"
        pooled_probs = _score_rows(clean_rows, final_scaler, final_model)
        pooled_y = [int(row.home_win) for row in clean_rows]
        pooled_metrics = _fold_metrics(pooled_y, pooled_probs)
        cv_accuracy = pooled_metrics["accuracy"]

    coefs = dict(zip(FEATURE_NAMES, [round(float(c), 6) for c in final_model.coef_[0]]))
    coefs["__intercept__"] = round(float(final_model.intercept_[0]), 6)
    coefs["__scaler_mean__"] = [round(float(v), 6) for v in final_scaler.mean_]
    coefs["__scaler_scale__"] = [round(float(v), 6) for v in final_scaler.scale_]
    ranked = sorted(
        ((feature, coef) for feature, coef in coefs.items() if not feature.startswith("__")),
        key=lambda item: abs(item[1]),
        reverse=True,
    )

    calibration_params = None
    calibration_summary = {
        "out_of_sample": False,
        "fit_seasons": [],
        "eval_season": None,
        "rows_fit": 0,
        "rows_eval": 0,
        "raw_brier_eval": None,
        "calibrated_brier_eval": None,
        "note": "Need at least two out-of-sample seasons to fit calibration on prior OOS predictions and evaluate on a later OOS season.",
    }
    oos_seasons = sorted({row["season"] for row in oos_predictions})
    if len(oos_seasons) >= 2:
        calibration_fit_seasons = oos_seasons[:-1]
        calibration_eval_season = oos_seasons[-1]
        fit_rows = [row for row in oos_predictions if row["season"] in calibration_fit_seasons]
        eval_rows = [row for row in oos_predictions if row["season"] == calibration_eval_season]
        if len(fit_rows) >= 30 and eval_rows:
            platt = LogisticRegression(max_iter=1000, random_state=42)
            fit_x = np.array([[row["raw_prob"]] for row in fit_rows], dtype=float)
            fit_y = np.array([row["actual"] for row in fit_rows], dtype=int)
            eval_x = np.array([[row["raw_prob"]] for row in eval_rows], dtype=float)
            eval_y = np.array([row["actual"] for row in eval_rows], dtype=int)
            platt.fit(fit_x, fit_y)
            calibration_params = {
                "a": round(float(platt.coef_[0][0]), 6),
                "b": round(float(platt.intercept_[0]), 6),
            }
            raw_brier_eval = float(brier_score_loss(eval_y, eval_x[:, 0]))
            cal_probs_eval = platt.predict_proba(eval_x)[:, 1]
            calibrated_brier_eval = float(brier_score_loss(eval_y, cal_probs_eval))
            calibration_summary = {
                "out_of_sample": True,
                "fit_seasons": calibration_fit_seasons,
                "eval_season": calibration_eval_season,
                "rows_fit": len(fit_rows),
                "rows_eval": len(eval_rows),
                "raw_brier_eval": round(raw_brier_eval, 4),
                "calibrated_brier_eval": round(calibrated_brier_eval, 4),
                "note": "Calibration is fit only on prior out-of-sample logistic probabilities and evaluated on a later out-of-sample season.",
            }

    incomplete_reason_counts: dict[str, int] = {}
    for row in rows:
        reasons = json.loads(row.incomplete_reasons_json) if row.incomplete_reasons_json else []
        for reason in reasons:
            incomplete_reason_counts[reason] = incomplete_reason_counts.get(reason, 0) + 1

    dataset_summary = {
        "point_in_time_policy": "game_start",
        "odds_snapshot_policy": _DEFAULT_ODDS_POLICY,
        "validation_mode": validation_mode,
        "date_range": {
            "start": min(row.game_date for row in rows).isoformat(),
            "end": max(row.game_date for row in rows).isoformat(),
        },
        "feature_set": FEATURE_NAMES,
        "rows_total": len(rows),
        "rows_features_complete": len(clean_rows),
        "rows_missing_features": len(rows) - len(clean_rows),
        "rows_with_historical_odds": sum(1 for row in rows if row.odds_complete),
        "rows_missing_historical_odds": sum(1 for row in rows if not row.odds_complete),
        "rows_skipped_for_training": len(rows) - len(clean_rows),
        "incomplete_reason_counts": incomplete_reason_counts,
    }
    validation_summary = {
        "method": validation_mode,
        "folds": folds,
        "pooled_out_of_sample_metrics": pooled_metrics,
        "cv_accuracy_mean": cv_accuracy,
        "calibration": calibration_summary,
    }
    limitations = [
        "Historical odds linkage only uses explicit pregame/open snapshots already stored locally in game_odds before each game start.",
        "Rows without reliable prior team or starter history are flagged and excluded from model training instead of being backfilled with season-end aggregates.",
        "Statcast-derived team sprint-speed and pitcher xERA features are excluded from the point-in-time model because the repo does not have a trustworthy historical pregame source for them.",
        "Final live coefficients are refit on all point-in-time-complete rows; calibration parameters are learned from prior out-of-sample logistic predictions only.",
    ]
    if validation_mode == "single_season_in_sample":
        limitations.append(
            "This run used a single-season in-sample fit because only one season was requested; accuracy/CV fields are not walk-forward estimates and no new calibration parameters were fit."
        )

    result = BacktestResult(
        seasons=",".join(str(season) for season in sorted(seasons)),
        n_games=len(clean_rows),
        accuracy=pooled_metrics["accuracy"],
        cv_accuracy=cv_accuracy,
        log_loss=pooled_metrics["log_loss"],
        brier_score=pooled_metrics["brier_score"],
        calibration_params_json=json.dumps(calibration_params) if calibration_params else None,
        coefficients_json=json.dumps(coefs),
        feature_ranks_json=json.dumps([{"feature": feature, "coef": coef} for feature, coef in ranked]),
        dataset_summary_json=json.dumps(dataset_summary),
        validation_summary_json=json.dumps(validation_summary),
        limitations_json=json.dumps(limitations),
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    if apply_weights:
        apply_backtest_weights(result)
    return result


def run_analysis(db: Session, seasons: list[int]) -> dict:
    rows = (
        db.query(BacktestGame)
        .filter(
            BacktestGame.season.in_(seasons),
            BacktestGame.home_win.isnot(None),
            BacktestGame.features_complete == True,  # noqa: E712
        )
        .order_by(BacktestGame.feature_cutoff_time.asc(), BacktestGame.game_id.asc())
        .all()
    )
    if not rows:
        return {"error": "No point-in-time-complete backtest rows found. Run /api/backtest/collect first."}

    records = []
    for row in rows:
        vector = dict(zip(FEATURE_NAMES, _row_to_feature_vector(row)))
        vector["home_win"] = int(row.home_win)
        vector["season"] = row.season
        vector["odds_complete"] = bool(row.odds_complete)
        records.append(vector)

    feature_correlations = []
    y = np.array([record["home_win"] for record in records], dtype=float)
    for feature_name in FEATURE_NAMES:
        x = np.array([record[feature_name] for record in records], dtype=float)
        corr = float(np.corrcoef(x, y)[0, 1]) if np.std(x) > 0 else 0.0
        if math.isnan(corr):
            corr = 0.0
        feature_correlations.append({
            "feature": feature_name,
            "pearson_r": round(corr, 4),
            "abs_r": round(abs(corr), 4),
        })
    feature_correlations.sort(key=lambda row: row["abs_r"], reverse=True)

    season_breakdown = []
    for season in sorted({row.season for row in rows}):
        season_rows = [row for row in rows if row.season == season]
        season_breakdown.append({
            "season": season,
            "games": len(season_rows),
            "home_win_rate": round(float(sum(int(row.home_win) for row in season_rows) / len(season_rows)), 4),
            "odds_complete_rate": round(float(sum(int(bool(row.odds_complete)) for row in season_rows) / len(season_rows)), 4),
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "point_in_time_policy": "game_start",
        "odds_snapshot_policy": _DEFAULT_ODDS_POLICY,
        "n_games": len(rows),
        "feature_correlations": feature_correlations,
        "v05_signal_correlations": _v05_signal_correlations(db, seasons),
        "season_breakdown": season_breakdown,
        "rows_missing_odds": sum(1 for row in rows if not row.odds_complete),
    }


def _pearson_r(values: list[float], outcomes: list[float]) -> float:
    if len(values) < 2 or len(values) != len(outcomes):
        return 0.0
    x = np.array(values, dtype=float)
    y = np.array(outcomes, dtype=float)
    if np.std(x) == 0 or np.std(y) == 0:
        return 0.0
    corr = float(np.corrcoef(x, y)[0, 1])
    return 0.0 if math.isnan(corr) else corr


def _v05_signal_correlations(db: Session, seasons: list[int]) -> dict:
    """
    Read-only v0.5 shadow-mode validation.

    The live travel feature currently represents the away team's travel stress,
    so the public "travel_stress" row maps to sandbox_predictions_v4.travel_stress_away.
    """
    rows = (
        db.query(SandboxPredictionV4, GameOutcomeReview)
        .join(GameOutcomeReview, GameOutcomeReview.game_id == SandboxPredictionV4.game_id)
        .filter(
            SandboxPredictionV4.season.in_(seasons),
            GameOutcomeReview.final_home_score.isnot(None),
            GameOutcomeReview.final_away_score.isnot(None),
        )
        .order_by(SandboxPredictionV4.game_date.asc(), SandboxPredictionV4.game_id.asc())
        .all()
    )
    if not rows:
        return {
            "n_games": 0,
            "threshold_abs_r": 0.04,
            "recommendation": "No reviewed v0.5 sandbox rows found yet; keep collecting shadow-mode data.",
            "signals": [],
        }

    signal_getters = {
        "travel_stress": lambda pred: pred.travel_stress_away,
        "travel_stress_home": lambda pred: pred.travel_stress_home,
        "travel_stress_adv": lambda pred: (
            (pred.travel_stress_home or 0.0) - (pred.travel_stress_away or 0.0)
            if pred.travel_stress_home is not None or pred.travel_stress_away is not None
            else None
        ),
        "series_game_number": lambda pred: pred.series_game_number,
        "is_series_opener": lambda pred: 1.0 if pred.is_series_opener else 0.0,
        "is_series_finale": lambda pred: 1.0 if pred.is_series_finale else 0.0,
    }

    signals = []
    for signal_name, getter in signal_getters.items():
        values = []
        outcomes = []
        for prediction, review in rows:
            value = getter(prediction)
            if value is None:
                continue
            values.append(float(value))
            outcomes.append(1.0 if review.final_home_score > review.final_away_score else 0.0)

        corr = _pearson_r(values, outcomes)
        abs_r = abs(corr)
        signals.append({
            "signal": signal_name,
            "n": len(values),
            "pearson_r": round(corr, 4),
            "abs_r": round(abs_r, 4),
            "candidate_for_feature_set": abs_r > 0.04,
        })

    signals.sort(key=lambda row: row["abs_r"], reverse=True)
    candidates = [row["signal"] for row in signals if row["candidate_for_feature_set"]]
    recommendation = (
        f"Candidate v0.5 signals above threshold: {', '.join(candidates)}. Review before changing FEATURE_NAMES or retraining."
        if candidates
        else "No v0.5 signal cleared abs(pearson_r) > 0.04; keep v3 feature set unchanged."
    )

    return {
        "n_games": len(rows),
        "threshold_abs_r": 0.04,
        "target": "home_win",
        "recommendation": recommendation,
        "signals": signals,
    }


def get_latest_calibration_params(db: Session) -> dict | None:
    result = (
        db.query(BacktestResult)
        .filter(BacktestResult.calibration_params_json.isnot(None))
        .order_by(BacktestResult.run_at.desc())
        .first()
    )
    if result and result.calibration_params_json:
        return json.loads(result.calibration_params_json)
    return None


def get_latest_calibration_result(db: Session) -> BacktestResult | None:
    return (
        db.query(BacktestResult)
        .filter(BacktestResult.calibration_params_json.isnot(None))
        .order_by(BacktestResult.run_at.desc())
        .first()
    )


def apply_calibration(raw_home: float, raw_away: float, params: dict) -> tuple[float, float]:
    a, b = params["a"], params["b"]
    cal_home = 1.0 / (1.0 + math.exp(-(a * raw_home + b)))
    cal_away = 1.0 / (1.0 + math.exp(-(a * raw_away + b)))
    total = cal_home + cal_away
    if total == 0:
        return raw_home, raw_away
    return round(cal_home / total, 4), round(cal_away / total, 4)


def apply_backtest_weights(result: BacktestResult) -> None:
    from app.services.simulator import set_weights

    coefs = json.loads(result.coefficients_json)
    run_diff_abs = abs(coefs.get("run_diff_adv", 0.26))
    pythag_proxy_abs = abs(coefs.get("pythagorean_win_pct_adv", 0.22))

    if run_diff_abs + pythag_proxy_abs == 0:
        return

    set_weights(0.0, run_diff_abs, pythag_proxy_abs)
    print(
        f"[backtest] Simulator weights updated — OPS: 0.0000 (removed)  "
        f"RUN_DIFF: {run_diff_abs:.4f}  PYTHAG: {pythag_proxy_abs:.4f}",
        flush=True,
    )
