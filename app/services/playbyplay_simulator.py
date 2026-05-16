from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.config import MLB_API_BASE
from app.models.schema import Game, Prediction, SandboxPredictionV4


MODEL_VERSION = "v0.6-pbp-shadow"
MAX_EVENTS = 96


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


def _iso_or_raw(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _event_weights(target_runs: float, sandbox: SandboxPredictionV4 | None, *, is_home: bool) -> list[tuple[str, float]]:
    run_factor = max(0.72, min(1.38, target_runs / 4.2))
    wind_factor = float(sandbox.wind_factor or 0.0) if sandbox and sandbox.wind_factor is not None else 0.0
    bullpen_boost = 0.0
    if sandbox:
        strength = sandbox.home_bullpen_strength if is_home else sandbox.away_bullpen_strength
        if strength is not None:
            bullpen_boost = max(-0.025, min(0.025, (0.55 - float(strength)) * 0.05))

    hr = max(0.018, min(0.062, 0.032 * run_factor + (wind_factor * 0.018)))
    double = max(0.030, min(0.072, 0.045 * run_factor + (wind_factor * 0.006)))
    walk = max(0.060, min(0.120, 0.083 * run_factor + bullpen_boost))
    single = max(0.115, min(0.190, 0.148 * run_factor))
    triple = max(0.002, min(0.010, 0.005 * run_factor))
    strikeout = max(0.165, min(0.280, 0.225 - ((run_factor - 1.0) * 0.045)))
    generic_out = max(0.280, 1.0 - (hr + double + walk + single + triple + strikeout))
    return [
        ("home_run", hr),
        ("triple", triple),
        ("double", double),
        ("single", single),
        ("walk", walk),
        ("strikeout", strikeout),
        ("out", generic_out),
    ]


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
) -> str:
    expected_so_far = state.target_runs * (inning / 9.0)
    if state.runs < expected_so_far - 1.2 and state.outs < 2 and rng.random() < 0.22:
        return rng.choice(["single", "double", "walk"])
    if state.runs > expected_so_far + 1.5 and rng.random() < 0.24:
        return rng.choice(["strikeout", "out"])
    return outcome


def _advance_runners(bases: list[bool], outcome: str) -> tuple[list[bool], int, int]:
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
        runs += int(third) + int(second)
        rbi += int(third) + int(second)
        return [True, first, False], runs, rbi
    if outcome == "double":
        runs += int(third) + int(second) + int(first)
        rbi += int(third) + int(second) + int(first)
        return [False, True, False], runs, rbi
    if outcome == "triple":
        runs += int(third) + int(second) + int(first)
        rbi += int(third) + int(second) + int(first)
        return [False, False, True], runs, rbi
    if outcome == "home_run":
        runs += 1 + int(third) + int(second) + int(first)
        rbi += runs
        return [False, False, False], runs, rbi
    return bases, 0, 0


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
    sandbox = _latest_sandbox(db, game_id)
    away_target = float(prediction.projected_away_score) if prediction and prediction.projected_away_score is not None else 4.2
    home_target = float(prediction.projected_home_score) if prediction and prediction.projected_home_score is not None else 4.4

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
            weights = _event_weights(state.target_runs, sandbox, is_home=is_home_batting)
            batter_slot = 1
            while state.outs < 3 and event_id <= MAX_EVENTS:
                before_score = score_home - score_away
                outcome = _force_run_pressure(_choose(rng, weights), state, inning, rng)
                if outcome in {"strikeout", "out"}:
                    state.outs += 1
                    runs = 0
                    rbi = 0
                else:
                    state.bases, runs, rbi = _advance_runners(state.bases, outcome)
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
                        "commentary": _event_commentary(outcome, runs, state.batting_team, inning, half),
                    }
                )
                event_id += 1
                batter_slot = 1 + (batter_slot % 9)

    highlights = [e for e in events if e["is_highlight"]]
    if len(highlights) < 6:
        highlights = sorted(events, key=lambda e: (e["runs"], e["outcome"] == "home_run"), reverse=True)[:6]

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
            "total": round(away_target + home_target, 2),
            "source": "latest_active_prediction" if prediction else "fallback_league_average",
        },
        "simulated_final": {"away": score_away, "home": score_home, "total": score_away + score_home},
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
    counter = Counter(e.get("outcome") for e in events)
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
        "runs_by_inning": dict(sorted(runs_by_inning.items())),
    }


def compare_sim_to_actual(db: Session, game_id: int) -> dict[str, Any]:
    sim = simulate_play_by_play(db, game_id)
    actual = fetch_actual_play_by_play(game_id)
    if sim.get("status") != "ok" or actual.get("status") != "ok" or not actual.get("events"):
        return {"status": "not_ready", "game_id": game_id, "simulation": sim, "actual": actual, "lessons": []}

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

    return {
        "status": "ok",
        "game_id": game_id,
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
