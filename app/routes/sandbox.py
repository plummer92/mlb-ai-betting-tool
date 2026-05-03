"""
v0.4 Sandbox API routes.
All read-only except POST /api/sandbox/grade.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import Game, SandboxPredictionV4, UmpireAssignmentV4

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])


# ── 1. Today's sandbox predictions ──────────────────────────────────────────

@router.get("/predictions/today")
def get_today_sandbox_predictions(db: Session = Depends(get_db)):
    today = date.today()
    rows = (
        db.query(SandboxPredictionV4)
        .filter(SandboxPredictionV4.game_date == today)
        .order_by(SandboxPredictionV4.created_at.desc())
        .all()
    )
    return [_pred_to_dict(r) for r in rows]


# ── 2. Performance summary ───────────────────────────────────────────────────

@router.get("/performance")
def get_sandbox_performance(db: Session = Depends(get_db)):
    graded = (
        db.query(SandboxPredictionV4)
        .filter(SandboxPredictionV4.f5_result.isnot(None))
        .all()
    )

    f5_wins = sum(1 for r in graded if r.f5_result == "WIN")
    f5_losses = sum(1 for r in graded if r.f5_result == "LOSS")
    fg_graded = [r for r in graded if r.full_game_result is not None]
    fg_wins = sum(1 for r in fg_graded if r.full_game_result == "WIN")
    fg_losses = sum(1 for r in fg_graded if r.full_game_result == "LOSS")

    conv_graded = [r for r in graded if r.bullpen_convergence and r.f5_result]
    conv_wins = sum(1 for r in conv_graded if r.f5_result == "WIN")

    agree_graded = [r for r in graded if r.v3_v4_agreement and r.f5_result]
    agree_wins = sum(1 for r in agree_graded if r.f5_result == "WIN")

    def win_rate(wins: int, total: int) -> Optional[float]:
        return round(wins / total, 4) if total > 0 else None

    return {
        "f5_record": f"{f5_wins}W-{f5_losses}L",
        "f5_win_rate": win_rate(f5_wins, f5_wins + f5_losses),
        "full_game_record": f"{fg_wins}W-{fg_losses}L",
        "full_game_win_rate": win_rate(fg_wins, fg_wins + fg_losses),
        "convergence_win_rate": win_rate(conv_wins, len(conv_graded)),
        "v3_agreement_win_rate": win_rate(agree_wins, len(agree_graded)),
        "total_graded": len(graded),
        "weekly_roi": _compute_weekly_roi(graded),
    }


def _compute_weekly_roi(rows: list) -> list:
    """Rolling 7-day win rates for F5 and full game."""
    from collections import defaultdict
    by_date: dict = defaultdict(lambda: {"f5_w": 0, "f5_l": 0, "fg_w": 0, "fg_l": 0})
    for r in rows:
        if r.game_date:
            d = str(r.game_date)
            if r.f5_result == "WIN":
                by_date[d]["f5_w"] += 1
            elif r.f5_result == "LOSS":
                by_date[d]["f5_l"] += 1
            if r.full_game_result == "WIN":
                by_date[d]["fg_w"] += 1
            elif r.full_game_result == "LOSS":
                by_date[d]["fg_l"] += 1

    result = []
    for d in sorted(by_date.keys())[-7:]:
        v = by_date[d]
        f5_total = v["f5_w"] + v["f5_l"]
        fg_total = v["fg_w"] + v["fg_l"]
        result.append({
            "date": d,
            "f5_win_rate": round(v["f5_w"] / f5_total, 4) if f5_total else None,
            "full_game_win_rate": round(v["fg_w"] / fg_total, 4) if fg_total else None,
        })
    return result


# ── 3. High-conviction plays (convergence + agreement) ───────────────────────

@router.get("/convergence/today")
def get_convergence_plays(db: Session = Depends(get_db)):
    today = date.today()
    rows = (
        db.query(SandboxPredictionV4)
        .filter(
            SandboxPredictionV4.game_date == today,
            SandboxPredictionV4.v3_v4_agreement == True,   # noqa: E712
            SandboxPredictionV4.bullpen_convergence == True,  # noqa: E712
        )
        .order_by(SandboxPredictionV4.v4_confidence.desc())
        .all()
    )
    return [
        {**_pred_to_dict(r), "conviction": "HIGH CONVICTION"}
        for r in rows
    ]


# ── 4. Umpire impact table ───────────────────────────────────────────────────

@router.get("/umpires")
def get_umpires(db: Session = Depends(get_db)):
    rows = (
        db.query(UmpireAssignmentV4)
        .order_by(UmpireAssignmentV4.run_expectancy_impact.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "game_id": r.game_id,
            "umpire_name": r.umpire_name,
            "run_expectancy_impact": r.run_expectancy_impact,
            "historical_k_rate_delta": r.historical_k_rate_delta,
            "season": r.season,
            "collected_at": r.collected_at.isoformat() if r.collected_at else None,
        }
        for r in rows
    ]


# ── 5. Grade endpoint ────────────────────────────────────────────────────────

@router.post("/grade")
def grade_sandbox_predictions(db: Session = Depends(get_db)):
    """
    Grade ungraded sandbox predictions by comparing projections to actual scores.
    Updates f5_result and full_game_result.
    """
    ungraded = (
        db.query(SandboxPredictionV4)
        .filter(SandboxPredictionV4.f5_result.is_(None))
        .all()
    )
    graded_count = 0
    for pred in ungraded:
        game = db.query(Game).filter(Game.game_id == pred.game_id).first()
        if not game:
            continue
        if game.final_away_score is None or game.final_home_score is None:
            continue

        actual_total = game.final_away_score + game.final_home_score
        now = datetime.now(timezone.utc)

        # Grade F5 — approximate first 5 innings as ~55% of final total
        if pred.f5_projected_total is not None and pred.f5_line is not None:
            actual_f5_approx = actual_total * 0.55
            if pred.f5_pick == "OVER":
                pred.f5_result = "WIN" if actual_f5_approx > pred.f5_line else "LOSS"
            elif pred.f5_pick == "UNDER":
                pred.f5_result = "WIN" if actual_f5_approx < pred.f5_line else "LOSS"
            pred.f5_graded_at = now

        # Grade full game
        if pred.full_game_projected_total is not None and pred.v3_projected_total is not None:
            v4_line = pred.v3_projected_total  # use v3 as the line reference
            if pred.full_game_projected_total > v4_line + 0.3:
                pred.full_game_result = "WIN" if actual_total > v4_line else "LOSS"
            elif pred.full_game_projected_total < v4_line - 0.3:
                pred.full_game_result = "WIN" if actual_total < v4_line else "LOSS"
            else:
                pred.full_game_result = "PUSH"
            pred.full_game_graded_at = now

        graded_count += 1

    db.commit()
    return {"graded": graded_count, "remaining_ungraded": len(ungraded) - graded_count}


# ── 6. Today's predictions with all v0.5 signal fields ──────────────────────

@router.get("/signals/today")
def get_signals_today(db: Session = Depends(get_db)):
    """Today's sandbox predictions with all v0.5 signal fields for the Signal Intelligence panel."""
    today = date.today()
    rows = (
        db.query(SandboxPredictionV4)
        .filter(SandboxPredictionV4.game_date == today)
        .order_by(SandboxPredictionV4.created_at.desc())
        .all()
    )
    game_ids = [r.game_id for r in rows if r.game_id]
    games_by_id: dict = {}
    if game_ids:
        for g in db.query(Game).filter(Game.game_id.in_(game_ids)).all():
            games_by_id[g.game_id] = g
    return [_signal_to_dict(r, games_by_id.get(r.game_id)) for r in rows]


def _signal_to_dict(r: SandboxPredictionV4, game=None) -> dict:
    parts = []
    if r.wind_factor is not None:
        if r.wind_factor < -0.3:
            parts.append("strong wind blowing in suppresses scoring")
        elif r.wind_factor > 0.3:
            parts.append("wind blowing out to CF elevates run scoring")
    if r.travel_stress_away is not None and r.travel_stress_away > 0.25:
        parts.append(f"away team carrying {int(r.travel_stress_away * 100)}% travel stress")
    if r.is_series_opener:
        parts.append("series opener — starters fresh, bullpens reset")
    if r.is_series_finale:
        parts.append("series finale — potential lineup and bullpen fatigue")
    if r.public_bias_edge is not None:
        if r.public_bias_edge >= 0.06:
            parts.append("weekend home-dog fade opportunity")
        elif r.public_bias_edge <= -0.04:
            parts.append("public favorite inflated — weekday fade edge on dog")
    explanation = "; ".join(parts) if parts else "No significant signals detected."

    base = _pred_to_dict(r)
    base.update({
        "wind_factor": r.wind_factor,
        "temp_f": r.temp_f,
        "humidity_pct": r.humidity_pct,
        "is_dome": r.is_dome,
        "travel_stress_home": r.travel_stress_home,
        "travel_stress_away": r.travel_stress_away,
        "series_game_number": r.series_game_number,
        "is_series_opener": r.is_series_opener,
        "is_series_finale": r.is_series_finale,
        "public_bias_edge": r.public_bias_edge,
        "start_time": game.start_time if game else None,
        "explanation": explanation,
    })
    return base


# ── Helpers ──────────────────────────────────────────────────────────────────

def _pred_to_dict(r: SandboxPredictionV4) -> dict:
    return {
        "id": r.id,
        "game_id": r.game_id,
        "game_date": str(r.game_date) if r.game_date else None,
        "season": r.season,
        "away_team": r.away_team,
        "home_team": r.home_team,
        "f5_projected_total": r.f5_projected_total,
        "f5_line": r.f5_line,
        "f5_pick": r.f5_pick,
        "f5_edge_pct": r.f5_edge_pct,
        "umpire_name": r.umpire_name,
        "umpire_run_impact": r.umpire_run_impact,
        "home_bullpen_strength": r.home_bullpen_strength,
        "away_bullpen_strength": r.away_bullpen_strength,
        "bullpen_convergence": r.bullpen_convergence,
        "full_game_projected_total": r.full_game_projected_total,
        "v3_projected_total": r.v3_projected_total,
        "v3_home_win_pct": r.v3_home_win_pct,
        "v4_home_win_pct": r.v4_home_win_pct,
        "v4_confidence": r.v4_confidence,
        "v3_v4_agreement": r.v3_v4_agreement,
        "f5_result": r.f5_result,
        "full_game_result": r.full_game_result,
        "f5_graded_at": r.f5_graded_at.isoformat() if r.f5_graded_at else None,
        "full_game_graded_at": r.full_game_graded_at.isoformat() if r.full_game_graded_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
