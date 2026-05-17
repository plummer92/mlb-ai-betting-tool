from __future__ import annotations

import random
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.config import MLB_API_BASE
from app.models.schema import (
    Game,
    ManagerTendency,
    PlayByPlayComparison,
    Prediction,
    RelieverWorkload,
    SandboxPredictionV4,
)
from app.services.sandbox_simulator import run_v4_sandbox


MODEL_VERSION = "v0.6-pbp-shadow"
MAX_EVENTS = 96
SIM_OUTCOMES = ("home_run", "triple", "double", "single", "walk", "strikeout", "out")
PBP_SHADOW_MULTIPLIERS = {
    # 2026 shadow backtest through 2026-05-16, 60-game sample.
    # Used only by the visual play-by-play engine, not by betting picks.
    "home_run": 1.213,
    "triple": 0.650,
    "double": 1.219,
    "single": 1.167,
    "walk": 1.450,
    "strikeout": 0.902,
    "out": 0.900,
}
PRODUCTIVE_OUTCOMES = {"home_run", "triple", "double", "single", "walk"}


@dataclass
class HalfState:
    inning: int
    half: str
    batting_team: str
    pitching_team: str
    pitcher: str | None
    target_runs: float
    score_away: int
    score_home: int
    bases: list[bool]
    outs: int = 0
    runs: int = 0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _latest_prediction(db: Session, game_id: int) -> Prediction | None:
    return (
        db.query(Prediction)
        .filter(Prediction.game_id == game_id, Prediction.is_active.is_(True))
        .order_by(Prediction.prediction_id.desc())
        .first()
    )


def _latest_sandbox(db: Session, game_id: int) -> SandboxPredictionV4 | None:
    try:
        return (
            db.query(SandboxPredictionV4)
            .filter(SandboxPredictionV4.game_id == game_id)
            .order_by(SandboxPredictionV4.created_at.desc())
            .first()
        )
    except Exception:
        return None


def _refresh_sandbox_context(db: Session, game_id: int) -> dict[str, Any]:
    try:
        result = run_v4_sandbox(game_id, db)
        return {
            "attempted": True,
            "ok": bool(result),
            "error": None,
        }
    except Exception as exc:
        return {
            "attempted": True,
            "ok": False,
            "error": str(exc),
        }


def _iso_or_raw(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _event_weights(
    target_runs: float,
    sandbox: SandboxPredictionV4 | None,
    *,
    is_home: bool,
    multipliers: dict[str, float] | None = None,
) -> list[tuple[str, float]]:
    run_factor = _clamp(target_runs / 4.2, 0.72, 1.32)
    wind_factor = float(sandbox.wind_factor or 0.0) if sandbox and sandbox.wind_factor is not None else 0.0
    umpire_impact = float(sandbox.umpire_run_impact or 0.0) if sandbox and sandbox.umpire_run_impact is not None else 0.0
    bullpen_boost = 0.0
    if sandbox:
        # The batting team is affected by the opposing bullpen, not its own.
        strength = sandbox.away_bullpen_strength if is_home else sandbox.home_bullpen_strength
        if strength is not None:
            bullpen_boost = _clamp((0.55 - float(strength)) * 0.05, -0.025, 0.025)

    hr = _clamp(0.028 * run_factor + (wind_factor * 0.014), 0.015, 0.054)
    double = _clamp(0.041 * run_factor + (wind_factor * 0.005), 0.028, 0.066)
    walk = _clamp(0.078 * run_factor + bullpen_boost + (umpire_impact * 0.008), 0.054, 0.114)
    single = _clamp(0.143 * run_factor, 0.110, 0.184)
    triple = _clamp(0.004 * run_factor, 0.001, 0.009)
    strikeout = _clamp(0.232 - ((run_factor - 1.0) * 0.035) - (umpire_impact * 0.006), 0.170, 0.290)
    generic_out = max(0.280, 1.0 - (hr + double + walk + single + triple + strikeout))
    base_weights = {
        "home_run": hr,
        "triple": triple,
        "double": double,
        "single": single,
        "walk": walk,
        "strikeout": strikeout,
        "out": generic_out,
    }
    return [
        (name, max(0.001, base_weights[name] * (multipliers or PBP_SHADOW_MULTIPLIERS).get(name, 1.0)))
        for name in SIM_OUTCOMES
    ]


def _weight_map(
    target_runs: float,
    sandbox: SandboxPredictionV4 | None,
    *,
    is_home: bool,
    multipliers: dict[str, float] | None = None,
) -> dict[str, float]:
    weights = dict(_event_weights(target_runs, sandbox, is_home=is_home, multipliers=multipliers))
    total = sum(weights.values()) or 1.0
    return {name: weights.get(name, 0.0) / total for name in SIM_OUTCOMES}


def _choose(rng: random.Random, weights: list[tuple[str, float]]) -> str:
    total = sum(w for _, w in weights)
    pick = rng.random() * total
    upto = 0.0
    for name, weight in weights:
        upto += weight
        if pick <= upto:
            return name
    return weights[-1][0]


def _force_run_pressure(
    outcome: str,
    state: HalfState,
    inning: int,
    rng: random.Random,
) -> tuple[str, str | None]:
    batting_runs = state.score_home if state.half == "bottom" else state.score_away
    inning_progress = ((inning - 1) + ((state.outs + 1) / 6.0)) / 9.0
    expected_so_far = state.target_runs * _clamp(inning_progress, 0.05, 1.0)
    ahead_of_pace = batting_runs - expected_so_far
    behind_pace = expected_so_far - batting_runs

    productive = outcome in {"walk", "single", "double", "triple", "home_run"}
    if batting_runs >= state.target_runs + 2.0 and productive and rng.random() < 0.58:
        return rng.choice(["strikeout", "out"]), "suppressed_hot_pace"
    if ahead_of_pace > 1.75 and productive and rng.random() < _clamp(0.26 + (ahead_of_pace * 0.06), 0.26, 0.58):
        return rng.choice(["strikeout", "out"]), "suppressed_hot_pace"
    if behind_pace > 0.75 and batting_runs < state.target_runs - 0.4 and state.outs < 2:
        pressure = _clamp(0.18 + (behind_pace * 0.05), 0.18, 0.34)
        if rng.random() < pressure:
            return rng.choice(["single", "walk", "double"]), "created_catchup_pressure"
    if behind_pace > 1.6 and batting_runs < state.target_runs - 0.8 and rng.random() < 0.16:
        return rng.choice(["single", "walk", "double"]), "created_catchup_pressure"
    return outcome, None


def _advance_runners(bases: list[bool], outcome: str, rng: random.Random) -> tuple[list[bool], int, int]:
    runs = 0
    rbi = 0
    first, second, third = bases
    if outcome == "walk":
        if first and second and third:
            runs += 1
            rbi += 1
        new_third = third or (first and second)
        new_second = second or first
        return [True, new_second, new_third], runs, rbi
    if outcome == "single":
        second_scores = second and rng.random() < 0.64
        first_to_third = first and rng.random() < 0.38
        runs += int(third) + int(second_scores)
        rbi += int(third) + int(second_scores)
        return [True, first and not first_to_third, first_to_third or (second and not second_scores)], runs, rbi
    if outcome == "double":
        first_scores = first and rng.random() < 0.70
        runs += int(third) + int(second) + int(first_scores)
        rbi += int(third) + int(second) + int(first_scores)
        return [False, True, first and not first_scores], runs, rbi
    if outcome == "triple":
        runs += int(third) + int(second) + int(first)
        rbi += int(third) + int(second) + int(first)
        return [False, False, True], runs, rbi
    if outcome == "home_run":
        runs += 1 + int(third) + int(second) + int(first)
        rbi += runs
        return [False, False, False], runs, rbi
    return bases, 0, 0


def _safe_bullpen_report(db: Session, team_id: int | None) -> dict[str, Any] | None:
    if team_id is None:
        return None
    try:
        target_date = date.today()
        three_days_ago = target_date - timedelta(days=3)
        rows = (
            db.query(RelieverWorkload)
            .filter(
                RelieverWorkload.team_id == team_id,
                RelieverWorkload.date >= three_days_ago,
                RelieverWorkload.date < target_date,
            )
            .all()
        )
        manager = db.query(ManagerTendency).filter(ManagerTendency.team_id == team_id).first()
        team_game = (
            db.query(Game)
            .filter((Game.home_team_id == team_id) | (Game.away_team_id == team_id))
            .order_by(Game.game_date.desc())
            .first()
        )
        team_name = None
        if team_game:
            team_name = team_game.home_team if team_game.home_team_id == team_id else team_game.away_team

        player_pitches: dict[int, int] = {}
        player_latest: dict[int, date] = {}
        player_names: dict[int, str] = {}
        for row in rows:
            if row.player_id is None:
                continue
            player_pitches[row.player_id] = player_pitches.get(row.player_id, 0) + int(row.pitches_thrown or 0)
            if row.player_id not in player_latest or row.date > player_latest[row.player_id]:
                player_latest[row.player_id] = row.date
                player_names[row.player_id] = row.player_name or f"Player {row.player_id}"

        fatigued_arms = []
        fresh_arms = []
        for player_id, last_date in player_latest.items():
            days_rest = (target_date - last_date).days
            if days_rest < 2:
                fatigued_arms.append({
                    "name": player_names[player_id],
                    "days_rest": days_rest,
                    "pitches_last_3": player_pitches.get(player_id, 0),
                })
            elif days_rest >= 3:
                fresh_arms.append({"name": player_names[player_id], "days_rest": days_rest})

        last_3_days_pitches = sum(int(row.pitches_thrown or 0) for row in rows)
        b2b_rate = float(manager.b2b_usage_rate) if manager and manager.b2b_usage_rate is not None else 0.30
        bullpen_strength = _clamp(1.0 - (last_3_days_pitches / 180.0) - (b2b_rate * 0.18), 0.05, 1.0)
        if bullpen_strength < 0.30:
            fatigue_signal = "exhausted"
        elif bullpen_strength < 0.55:
            fatigue_signal = "tired"
        elif bullpen_strength < 0.75:
            fatigue_signal = "rested"
        else:
            fatigue_signal = "fresh"

        return {
            "team_id": team_id,
            "team_name": team_name or f"Team {team_id}",
            "bullpen_strength": round(bullpen_strength, 2),
            "fatigue_signal": fatigue_signal,
            "last_3_days_pitches": last_3_days_pitches,
            "manager_name": manager.manager_name if manager and manager.manager_name else "Unknown",
            "b2b_usage_rate": round(b2b_rate, 2),
            "fatigued_arms": sorted(fatigued_arms, key=lambda item: item["days_rest"])[:3],
            "fresh_arms": sorted(fresh_arms, key=lambda item: item["days_rest"], reverse=True)[:2],
        }
    except Exception:
        return None


def _event_label(outcome: str) -> str:
    return {
        "home_run": "Home Run",
        "triple": "Triple",
        "double": "Double",
        "single": "Single",
        "walk": "Walk",
        "strikeout": "Strikeout",
        "out": "Ball in Play Out",
    }.get(outcome, outcome)


def _normalize_actual_outcome(event_type: str | None, label: str | None = None) -> str:
    raw = (event_type or label or "").strip().lower().replace(" ", "_")
    if raw in {"home_run"}:
        return "home_run"
    if raw in {"triple"}:
        return "triple"
    if raw in {"double", "ground_rule_double"}:
        return "double"
    if raw in {"single"}:
        return "single"
    if raw in {"walk", "intent_walk", "hit_by_pitch"}:
        return "walk"
    if raw in {"strikeout", "strikeout_double_play"}:
        return "strikeout"
    return "out"


def _event_commentary(outcome: str, runs: int, team: str, inning: int, half: str) -> str:
    frame = f"{half.title()} {inning}"
    if outcome == "home_run":
        return f"{frame}: {team} changes the game state with a {runs}-run homer."
    if runs >= 2:
        return f"{frame}: {team} strings together damage for {runs} runs."
    if runs == 1:
        return f"{frame}: {team} cashes in a runner and nudges the total upward."
    if outcome in {"strikeout", "out"}:
        return f"{frame}: pitcher wins the plate appearance and keeps traffic contained."
    return f"{frame}: {team} adds pressure with traffic on the bases."


def simulate_play_by_play(db: Session, game_id: int) -> dict[str, Any]:
    game = db.query(Game).filter(Game.game_id == game_id).first()
    if not game:
        return {"status": "not_found", "game_id": game_id, "events": []}

    prediction = _latest_prediction(db, game_id)
    sandbox_refresh = _refresh_sandbox_context(db, game_id) if prediction else {
        "attempted": False,
        "ok": False,
        "error": "missing active prediction",
    }
    sandbox = _latest_sandbox(db, game_id)
    away_target = float(prediction.projected_away_score) if prediction and prediction.projected_away_score is not None else 4.2
    home_target = float(prediction.projected_home_score) if prediction and prediction.projected_home_score is not None else 4.4
    projected_total = round(away_target + home_target, 2)
    projection_bucket = _projection_bucket(projected_total)
    calibration = _bucketed_shadow_multipliers(db, projection_bucket)
    event_multipliers = calibration["multipliers"]

    rng = random.Random(f"{MODEL_VERSION}:{game_id}:{prediction.prediction_id if prediction else 'na'}")
    score_away = 0
    score_home = 0
    event_id = 1
    events: list[dict[str, Any]] = []
    inning_runs: dict[str, list[int]] = {game.away_team: [0] * 9, game.home_team: [0] * 9}
    score_swings: list[dict[str, Any]] = []

    for inning in range(1, 10):
        for half in ("top", "bottom"):
            is_home_batting = half == "bottom"
            state = HalfState(
                inning=inning,
                half=half,
                batting_team=game.home_team if is_home_batting else game.away_team,
                pitching_team=game.away_team if is_home_batting else game.home_team,
                pitcher=game.away_probable_pitcher if is_home_batting else game.home_probable_pitcher,
                target_runs=home_target if is_home_batting else away_target,
                score_away=score_away,
                score_home=score_home,
                bases=[False, False, False],
            )
            weights = _event_weights(
                state.target_runs,
                sandbox,
                is_home=is_home_batting,
                multipliers=event_multipliers,
            )
            batter_slot = 1
            while state.outs < 3 and event_id <= MAX_EVENTS:
                before_score = score_home - score_away
                outcome, governor_note = _force_run_pressure(_choose(rng, weights), state, inning, rng)
                if outcome in {"strikeout", "out"}:
                    state.outs += 1
                    runs = 0
                    rbi = 0
                else:
                    state.bases, runs, rbi = _advance_runners(state.bases, outcome, rng)
                    state.runs += runs
                    if is_home_batting:
                        score_home += runs
                    else:
                        score_away += runs

                after_score = score_home - score_away
                lead_change = (before_score <= 0 < after_score) or (before_score >= 0 > after_score)
                if lead_change:
                    score_swings.append({"inning": inning, "half": half, "team": state.batting_team})
                inning_runs[state.batting_team][inning - 1] += runs
                events.append(
                    {
                        "event_id": event_id,
                        "inning": inning,
                        "half": half,
                        "batting_team": state.batting_team,
                        "pitching_team": state.pitching_team,
                        "pitcher": state.pitcher,
                        "batter": f"{state.batting_team} #{batter_slot}",
                        "outcome": outcome,
                        "label": _event_label(outcome),
                        "runs": runs,
                        "rbi": rbi,
                        "outs_after": state.outs,
                        "bases_after": {"first": state.bases[0], "second": state.bases[1], "third": state.bases[2]},
                        "score": {"away": score_away, "home": score_home},
                        "is_highlight": bool(runs or outcome == "home_run" or lead_change),
                        "is_scoring_play": bool(runs),
                        "is_model_miss_clue": bool(governor_note or runs >= 2 or lead_change),
                        "governor_note": governor_note,
                        "commentary": _event_commentary(outcome, runs, state.batting_team, inning, half),
                    }
                )
                event_id += 1
                batter_slot = 1 + (batter_slot % 9)

    highlights = [e for e in events if e["is_highlight"]]
    if len(highlights) < 6:
        highlights = sorted(events, key=lambda e: (e["runs"], e["outcome"] == "home_run"), reverse=True)[:6]

    simulated_total = score_away + score_home
    projection_drift = round(simulated_total - projected_total, 1)
    away_report = _safe_bullpen_report(db, game.away_team_id)
    home_report = _safe_bullpen_report(db, game.home_team_id)

    return {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "game": {
            "game_id": game.game_id,
            "game_date": str(game.game_date),
            "away_team": game.away_team,
            "home_team": game.home_team,
            "away_pitcher": game.away_probable_pitcher,
            "home_pitcher": game.home_probable_pitcher,
            "venue": game.venue,
            "start_time": _iso_or_raw(game.start_time),
        },
        "projection": {
            "away_runs": away_target,
            "home_runs": home_target,
            "total": projected_total,
            "source": "latest_active_prediction" if prediction else "fallback_league_average",
            "pbp_calibration": {
                **calibration,
                "global_baseline": PBP_SHADOW_MULTIPLIERS,
            },
        },
        "simulated_final": {"away": score_away, "home": score_home, "total": simulated_total},
        "projection_drift": projection_drift,
        "context": {
            "uses_sandbox_signals": bool(sandbox),
            "sandbox_refresh": sandbox_refresh,
            "umpire": {
                "name": sandbox.umpire_name if sandbox else None,
                "run_impact": float(sandbox.umpire_run_impact) if sandbox and sandbox.umpire_run_impact is not None else 0.0,
            },
            "bullpen": {
                "away_strength": float(sandbox.away_bullpen_strength) if sandbox and sandbox.away_bullpen_strength is not None else (away_report or {}).get("bullpen_strength"),
                "home_strength": float(sandbox.home_bullpen_strength) if sandbox and sandbox.home_bullpen_strength is not None else (home_report or {}).get("bullpen_strength"),
                "convergence": bool(sandbox.bullpen_convergence) if sandbox else False,
                "away_report": away_report,
                "home_report": home_report,
            },
            "weather": {
                "wind_factor": float(sandbox.wind_factor) if sandbox and sandbox.wind_factor is not None else None,
                "temp_f": float(sandbox.temp_f) if sandbox and sandbox.temp_f is not None else None,
                "is_dome": bool(sandbox.is_dome) if sandbox and sandbox.is_dome is not None else None,
            },
        },
        "inning_runs": inning_runs,
        "score_swings": score_swings,
        "events": events,
        "highlights": highlights[:12],
    }


def fetch_actual_play_by_play(game_id: int) -> dict[str, Any]:
    url = f"{MLB_API_BASE.replace('/api/v1', '/api/v1.1')}/game/{game_id}/feed/live"
    resp = requests.get(url, timeout=30)
    if resp.status_code == 404:
        return {"status": "not_found", "game_id": game_id, "events": []}
    resp.raise_for_status()
    payload = resp.json()
    plays = (((payload.get("liveData") or {}).get("plays") or {}).get("allPlays") or [])
    game_data = payload.get("gameData") or {}
    teams = game_data.get("teams") or {}
    away_name = ((teams.get("away") or {}).get("name")) or "Away"
    home_name = ((teams.get("home") or {}).get("name")) or "Home"

    events = []
    for idx, play in enumerate(plays, start=1):
        about = play.get("about") or {}
        result = play.get("result") or {}
        matchup = play.get("matchup") or {}
        half = about.get("halfInning")
        inning = about.get("inning")
        is_top = half == "top"
        team = away_name if is_top else home_name
        rbi = int(result.get("rbi") or 0)
        events.append(
            {
                "event_id": idx,
                "inning": inning,
                "half": half,
                "batting_team": team,
                "batter": ((matchup.get("batter") or {}).get("fullName")),
                "pitcher": ((matchup.get("pitcher") or {}).get("fullName")),
                "outcome": result.get("eventType") or result.get("event") or "unknown",
                "sim_outcome": _normalize_actual_outcome(result.get("eventType"), result.get("event")),
                "label": result.get("event") or result.get("description") or "Play",
                "runs": rbi,
                "description": result.get("description") or "",
                "is_highlight": bool(rbi or result.get("eventType") in {"home_run", "triple", "double"}),
            }
        )
    return {
        "status": "ok",
        "game_id": game_id,
        "away_team": away_name,
        "home_team": home_name,
        "events": events,
        "highlights": [e for e in events if e["is_highlight"]][:12],
        "summary": _summarize_events(events),
    }


def _summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    counter = Counter(e.get("sim_outcome") or _normalize_actual_outcome(e.get("outcome"), e.get("label")) for e in events)
    runs_by_inning: dict[int, int] = defaultdict(int)
    for event in events:
        if event.get("inning"):
            runs_by_inning[int(event["inning"])] += int(event.get("runs") or 0)
    return {
        "plate_appearances": len(events),
        "runs": sum(int(e.get("runs") or 0) for e in events),
        "home_runs": counter.get("home_run", 0),
        "extra_base_hits": counter.get("double", 0) + counter.get("triple", 0) + counter.get("home_run", 0),
        "walks": counter.get("walk", 0),
        "strikeouts": counter.get("strikeout", 0),
        "outcome_counts": {name: counter.get(name, 0) for name in SIM_OUTCOMES},
        "runs_by_inning": dict(sorted(runs_by_inning.items())),
    }


def _latest_prediction_any(db: Session, game_id: int) -> Prediction | None:
    return (
        db.query(Prediction)
        .filter(Prediction.game_id == game_id)
        .order_by(Prediction.prediction_id.desc())
        .first()
    )


def _projection_bucket(projected_total: float | None) -> str:
    if projected_total is None:
        return "unknown"
    if projected_total < 7.5:
        return "low"
    if projected_total <= 9.0:
        return "normal"
    return "high"


def _bucketed_shadow_multipliers(db: Session, bucket: str) -> dict[str, Any]:
    rows = (
        db.query(PlayByPlayComparison)
        .filter(PlayByPlayComparison.projection_bucket == bucket)
        .order_by(PlayByPlayComparison.game_date.desc(), PlayByPlayComparison.compared_at.desc())
        .limit(40)
        .all()
    )
    if not rows:
        return {
            "mode": "shadow_global",
            "bucket": bucket,
            "sample_games": 0,
            "multipliers": PBP_SHADOW_MULTIPLIERS,
            "reason": "no stored comparisons for this bucket yet",
        }

    actual_counts = {name: 0 for name in SIM_OUTCOMES}
    sim_counts = {name: 0 for name in SIM_OUTCOMES}
    run_delta_sum = 0
    for row in rows:
        try:
            actual_summary = json.loads(row.actual_summary_json or "{}")
            sim_summary = json.loads(row.sim_summary_json or "{}")
        except json.JSONDecodeError:
            continue
        for name in SIM_OUTCOMES:
            actual_counts[name] += int((actual_summary.get("outcome_counts") or {}).get(name, 0) or 0)
            sim_counts[name] += int((sim_summary.get("outcome_counts") or {}).get(name, 0) or 0)
        run_delta_sum += int(row.run_delta or 0)

    avg_run_delta = run_delta_sum / len(rows)
    # Negative delta means actual scored fewer runs than the sim. Correct the run
    # environment first, then let event-shape ratios fine tune inside that.
    run_environment_factor = _clamp(1.0 + (avg_run_delta * 0.045), 0.78, 1.22)
    out_environment_factor = _clamp(1.0 - ((run_environment_factor - 1.0) * 0.45), 0.90, 1.10)

    multipliers = {}
    for name in SIM_OUTCOMES:
        if sim_counts[name] <= 0 or actual_counts[name] <= 0:
            event_adjusted = PBP_SHADOW_MULTIPLIERS[name]
        else:
            miss_ratio = _clamp(actual_counts[name] / sim_counts[name], 0.75, 1.25)
            blended_ratio = 1.0 + ((miss_ratio - 1.0) * 0.35)
            event_adjusted = PBP_SHADOW_MULTIPLIERS[name] * blended_ratio
        if name in PRODUCTIVE_OUTCOMES:
            multipliers[name] = round(_clamp(event_adjusted * run_environment_factor, 0.45, 1.60), 3)
            continue
        if name == "out":
            multipliers[name] = round(_clamp(event_adjusted * out_environment_factor, 0.60, 1.25), 3)
            continue
        multipliers[name] = round(_clamp(event_adjusted, 0.55, 1.60), 3)

    return {
        "mode": "shadow_bucket",
        "bucket": bucket,
        "sample_games": len(rows),
        "avg_run_delta": round(avg_run_delta, 2),
        "run_environment_factor": round(run_environment_factor, 3),
        "out_environment_factor": round(out_environment_factor, 3),
        "multipliers": multipliers,
        "reason": "run-total governor plus event-shape misses from stored comparisons in this projected-total bucket",
    }


def _empty_bucket() -> dict[str, Any]:
    return {
        "games": 0,
        "plate_appearances": 0,
        "runs": 0,
        "projected_total_sum": 0.0,
        "actual_total_sum": 0.0,
        "events": {name: 0 for name in SIM_OUTCOMES},
    }


def _finish_bucket(bucket: dict[str, Any], expected_rates: dict[str, float]) -> dict[str, Any]:
    plate_appearances = bucket["plate_appearances"] or 1
    actual_rates = {name: round(bucket["events"].get(name, 0) / plate_appearances, 4) for name in SIM_OUTCOMES}
    multipliers = {}
    for name in SIM_OUTCOMES:
        expected = expected_rates.get(name, 0.0)
        multipliers[name] = round(_clamp((actual_rates[name] / expected) if expected else 1.0, 0.65, 1.45), 3)

    games = bucket["games"] or 1
    projected_avg = bucket["projected_total_sum"] / games if bucket["projected_total_sum"] else None
    actual_avg = bucket["actual_total_sum"] / games if bucket["actual_total_sum"] else None
    return {
        "games": bucket["games"],
        "plate_appearances": bucket["plate_appearances"],
        "actual_event_rates": actual_rates,
        "expected_event_rates": {k: round(v, 4) for k, v in expected_rates.items()},
        "recommended_multipliers": multipliers,
        "avg_projected_total": round(projected_avg, 2) if projected_avg is not None else None,
        "avg_actual_total": round(actual_avg, 2) if actual_avg is not None else None,
        "avg_total_drift": round(actual_avg - projected_avg, 2) if projected_avg is not None and actual_avg is not None else None,
    }


def _store_pbp_comparison(
    db: Session,
    game: Game,
    sim: dict[str, Any],
    actual: dict[str, Any],
    sim_summary: dict[str, Any],
    actual_summary: dict[str, Any],
    lessons: list[dict[str, str]],
) -> None:
    projected_total = sim.get("projection", {}).get("total")
    run_delta = actual_summary["runs"] - sim_summary["runs"]
    payload = {
        "game_id": game.game_id,
        "game_date": game.game_date,
        "season": game.season,
        "model_version": sim.get("model_version") or MODEL_VERSION,
        "projection_bucket": _projection_bucket(projected_total),
        "projected_total": projected_total,
        "simulated_total": sim_summary["runs"],
        "actual_total": actual_summary["runs"],
        "run_delta": run_delta,
        "home_run_delta": actual_summary["home_runs"] - sim_summary["home_runs"],
        "walk_delta": actual_summary["walks"] - sim_summary["walks"],
        "strikeout_delta": actual_summary["strikeouts"] - sim_summary["strikeouts"],
        "sim_summary_json": json.dumps(sim_summary, sort_keys=True),
        "actual_summary_json": json.dumps(actual_summary, sort_keys=True),
        "context_json": json.dumps({
            "calibration": sim.get("projection", {}).get("pbp_calibration"),
            "game": sim.get("game"),
            "context": sim.get("context"),
        }, sort_keys=True),
        "lessons_json": json.dumps(lessons, sort_keys=True),
        "compared_at": datetime.utcnow(),
    }
    existing = db.query(PlayByPlayComparison).filter(PlayByPlayComparison.game_id == game.game_id).first()
    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
    else:
        db.add(PlayByPlayComparison(**payload))
    db.commit()


def backtest_play_by_play_weights(db: Session, season: int, limit: int = 120) -> dict[str, Any]:
    games = (
        db.query(Game)
        .filter(
            Game.season == season,
            Game.final_away_score.isnot(None),
            Game.final_home_score.isnot(None),
        )
        .order_by(Game.game_date.desc(), Game.game_id.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    buckets = {"overall": _empty_bucket(), "low": _empty_bucket(), "normal": _empty_bucket(), "high": _empty_bucket(), "unknown": _empty_bucket()}
    expected = {name: 0.0 for name in SIM_OUTCOMES}
    errors = []
    processed = 0

    for game in games:
        try:
            actual = fetch_actual_play_by_play(game.game_id)
            events = actual.get("events") or []
            if actual.get("status") != "ok" or not events:
                continue
        except Exception as exc:
            errors.append({"game_id": game.game_id, "error": str(exc)})
            continue

        prediction = _latest_prediction_any(db, game.game_id)
        sandbox = _latest_sandbox(db, game.game_id)
        away_target = float(prediction.projected_away_score) if prediction and prediction.projected_away_score is not None else None
        home_target = float(prediction.projected_home_score) if prediction and prediction.projected_home_score is not None else None
        projected_total = (
            away_target + home_target
            if away_target is not None and home_target is not None
            else (float(prediction.projected_total) if prediction and prediction.projected_total is not None else None)
        )
        actual_total = int(game.final_away_score or 0) + int(game.final_home_score or 0)
        bucket_name = _projection_bucket(projected_total)
        processed += 1

        away_weights = _weight_map(away_target or 4.2, sandbox, is_home=False)
        home_weights = _weight_map(home_target or 4.4, sandbox, is_home=True)
        game_expected = {name: (away_weights[name] + home_weights[name]) / 2.0 for name in SIM_OUTCOMES}
        for name, value in game_expected.items():
            expected[name] += value

        for target in (buckets["overall"], buckets[bucket_name]):
            target["games"] += 1
            target["plate_appearances"] += len(events)
            target["runs"] += sum(int(e.get("runs") or 0) for e in events)
            if projected_total is not None:
                target["projected_total_sum"] += projected_total
            target["actual_total_sum"] += actual_total
            for event in events:
                target["events"][_normalize_actual_outcome(event.get("outcome"), event.get("label"))] += 1

    expected_rates = {name: expected[name] / processed if processed else _weight_map(4.2, None, is_home=False)[name] for name in SIM_OUTCOMES}
    finished = {name: _finish_bucket(bucket, expected_rates) for name, bucket in buckets.items() if bucket["games"]}
    return {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "season": season,
        "games_considered": len(games),
        "games_processed": processed,
        "errors": errors[:12],
        "calibration": finished,
        "active_shadow_multipliers": PBP_SHADOW_MULTIPLIERS,
        "recommended_overall_multipliers": finished.get("overall", {}).get("recommended_multipliers", {}),
    }


def compare_sim_to_actual(db: Session, game_id: int) -> dict[str, Any]:
    sim = simulate_play_by_play(db, game_id)
    actual = fetch_actual_play_by_play(game_id)
    if sim.get("status") != "ok" or actual.get("status") != "ok" or not actual.get("events"):
        return {"status": "not_ready", "game_id": game_id, "simulation": sim, "actual": actual, "lessons": []}

    game = db.query(Game).filter(Game.game_id == game_id).first()
    sim_summary = _summarize_events(sim["events"])
    actual_summary = actual["summary"]
    lessons = []
    run_delta = actual_summary["runs"] - sim_summary["runs"]
    if abs(run_delta) >= 3:
        lessons.append({
            "type": "run_environment",
            "message": f"Actual run environment missed by {run_delta:+d} runs; inspect park, weather, starter command, and bullpen assumptions.",
        })
    if actual_summary["home_runs"] > sim_summary["home_runs"] + 1:
        lessons.append({"type": "power", "message": "Actual power damage exceeded simulation; check barrel/HR inputs and wind carry."})
    if actual_summary["walks"] > sim_summary["walks"] + 3:
        lessons.append({"type": "command", "message": "Actual walks exceeded simulation; pitcher command volatility may be under-modeled."})
    if actual_summary["strikeouts"] < sim_summary["strikeouts"] - 4:
        lessons.append({"type": "contact", "message": "Simulation expected more strikeouts than actual; lineup contact profile may be underrated."})

    stored = False
    if game:
        try:
            _store_pbp_comparison(db, game, sim, actual, sim_summary, actual_summary, lessons)
            stored = True
        except Exception as exc:
            db.rollback()
            lessons.append({"type": "memory", "message": f"Comparison generated but memory write failed: {exc}"})

    return {
        "status": "ok",
        "game_id": game_id,
        "stored": stored,
        "simulation_summary": sim_summary,
        "actual_summary": actual_summary,
        "deltas": {
            "runs": run_delta,
            "home_runs": actual_summary["home_runs"] - sim_summary["home_runs"],
            "walks": actual_summary["walks"] - sim_summary["walks"],
            "strikeouts": actual_summary["strikeouts"] - sim_summary["strikeouts"],
        },
        "lessons": lessons,
        "simulation": sim,
        "actual": actual,
    }
