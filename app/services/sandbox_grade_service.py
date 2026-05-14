from __future__ import annotations

from datetime import datetime, timezone

import requests
from sqlalchemy.orm import Session

from app.config import MLB_API_BASE
from app.models.schema import Game, SandboxPredictionV4


def _first_five_total_from_linescore(payload: dict) -> int | None:
    innings = payload.get("innings") or []
    first_five = [inning for inning in innings if int(inning.get("num") or 0) <= 5]
    if len(first_five) < 5:
        return None

    total = 0
    for inning in first_five:
        away = (inning.get("away") or {}).get("runs")
        home = (inning.get("home") or {}).get("runs")
        if away is None or home is None:
            return None
        total += int(away) + int(home)
    return total


def fetch_first_five_total(game_id: int) -> int | None:
    response = requests.get(f"{MLB_API_BASE}/game/{game_id}/linescore", timeout=20)
    response.raise_for_status()
    return _first_five_total_from_linescore(response.json())


def _pick_for_prediction(prediction: SandboxPredictionV4) -> str | None:
    pick = (prediction.f5_pick or "").upper()
    if pick in {"OVER", "UNDER"}:
        return pick
    if prediction.f5_projected_total is None or prediction.f5_line is None:
        return None
    if prediction.f5_projected_total > prediction.f5_line:
        return "OVER"
    if prediction.f5_projected_total < prediction.f5_line:
        return "UNDER"
    return None


def grade_f5_result(pick: str, actual_total: int, line: float) -> str:
    if actual_total == line:
        return "PUSH"
    if pick == "OVER":
        return "WIN" if actual_total > line else "LOSS"
    if pick == "UNDER":
        return "WIN" if actual_total < line else "LOSS"
    return "PUSH"


def grade_sandbox_f5_predictions(db: Session) -> dict:
    ungraded = (
        db.query(SandboxPredictionV4)
        .filter(SandboxPredictionV4.f5_result.is_(None))
        .order_by(SandboxPredictionV4.game_date.asc(), SandboxPredictionV4.game_id.asc())
        .all()
    )

    graded = 0
    skipped = 0
    errors = []
    now = datetime.now(timezone.utc)

    for prediction in ungraded:
        try:
            if prediction.game_id is None:
                skipped += 1
                continue
            if prediction.f5_line is None:
                skipped += 1
                continue
            pick = _pick_for_prediction(prediction)
            if pick is None:
                skipped += 1
                continue

            actual_f5_total = fetch_first_five_total(prediction.game_id)
            if actual_f5_total is None:
                skipped += 1
                continue

            prediction.f5_pick = pick
            prediction.f5_result = grade_f5_result(pick, actual_f5_total, float(prediction.f5_line))
            prediction.f5_graded_at = now
            graded += 1

            game = db.query(Game).filter(Game.game_id == prediction.game_id).first()
            if game and game.final_away_score is not None and game.final_home_score is not None:
                actual_total = game.final_away_score + game.final_home_score
                _grade_full_game_prediction(prediction, actual_total, now)
        except Exception as exc:
            skipped += 1
            errors.append({
                "game_id": prediction.game_id,
                "error": str(exc)[:200],
            })

    db.commit()
    return {
        "graded": graded,
        "skipped": skipped,
        "remaining_ungraded": max(len(ungraded) - graded, 0),
        "errors": errors[:10],
    }


def _grade_full_game_prediction(prediction: SandboxPredictionV4, actual_total: int, graded_at: datetime) -> None:
    if prediction.full_game_projected_total is None or prediction.v3_projected_total is None:
        return

    line = float(prediction.v3_projected_total)
    if prediction.full_game_projected_total > line + 0.3:
        prediction.full_game_result = "WIN" if actual_total > line else "LOSS"
    elif prediction.full_game_projected_total < line - 0.3:
        prediction.full_game_result = "WIN" if actual_total < line else "LOSS"
    else:
        prediction.full_game_result = "PUSH"
    prediction.full_game_graded_at = graded_at
