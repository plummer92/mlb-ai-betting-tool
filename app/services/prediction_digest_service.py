from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.schema import Game, Prediction
from app.services.notification_service import send_alert_message
from app.services.report_snapshot_service import get_report_snapshot, store_report_snapshot


PREDICTION_DIGEST_PREFIX = "prediction_discord_digest"
DISCORD_MESSAGE_LIMIT = 1900


def _report_name(run_stage: str) -> str:
    safe_stage = (run_stage or "latest").strip().lower().replace(" ", "_")
    return f"{PREDICTION_DIGEST_PREFIX}_{safe_stage}"


def _prob_pair(prediction: Prediction) -> tuple[float, float, str]:
    calibrated_away = prediction.calibrated_away_win_pct
    calibrated_home = prediction.calibrated_home_win_pct
    if calibrated_away is not None and calibrated_home is not None:
        return float(calibrated_away), float(calibrated_home), "calibrated"
    return float(prediction.away_win_pct), float(prediction.home_win_pct), "model"


def _pct(value: float | None) -> str:
    if value is None:
        return "---"
    return f"{float(value) * 100:.1f}%"


def _winner_pick(game: Game, away_prob: float, home_prob: float) -> tuple[str, float]:
    if away_prob >= home_prob:
        return game.away_team, away_prob
    return game.home_team, home_prob


def _confidence_band(confidence_gap: float) -> str:
    if confidence_gap >= 12:
        return "strong"
    if confidence_gap >= 7:
        return "medium"
    return "lean"


def _build_rows(db: Session, *, target_date: date, run_stage: str | None) -> list[dict[str, Any]]:
    query = (
        db.query(
            Prediction.game_id,
            func.max(Prediction.prediction_id).label("max_id"),
        )
        .join(Game, Game.game_id == Prediction.game_id)
        .filter(Game.game_date == target_date, Prediction.is_active.is_(True))
    )
    if run_stage:
        query = query.filter(Prediction.run_stage == run_stage)
    subq = query.group_by(Prediction.game_id).subquery()

    pairs = (
        db.query(Game, Prediction)
        .join(subq, Game.game_id == subq.c.game_id)
        .join(Prediction, Prediction.prediction_id == subq.c.max_id)
        .order_by(Game.start_time.asc(), Game.game_id.asc())
        .all()
    )

    rows: list[dict[str, Any]] = []
    for game, prediction in pairs:
        away_prob, home_prob, probability_source = _prob_pair(prediction)
        pick_team, pick_probability = _winner_pick(game, away_prob, home_prob)
        confidence_gap = abs(home_prob - away_prob) * 100
        rows.append(
            {
                "game_id": game.game_id,
                "game_date": game.game_date.isoformat(),
                "matchup": f"{game.away_team} @ {game.home_team}",
                "start_time": game.start_time,
                "away_team": game.away_team,
                "home_team": game.home_team,
                "away_win_pct": round(away_prob, 4),
                "home_win_pct": round(home_prob, 4),
                "probability_source": probability_source,
                "winner_pick": pick_team,
                "winner_probability": round(pick_probability, 4),
                "confidence_gap_pct": round(confidence_gap, 1),
                "confidence_band": _confidence_band(confidence_gap),
                "projected_away_score": round(float(prediction.projected_away_score), 1),
                "projected_home_score": round(float(prediction.projected_home_score), 1),
                "projected_total": round(float(prediction.projected_total), 1),
                "model_version": prediction.model_version,
                "run_stage": prediction.run_stage,
                "prediction_id": prediction.prediction_id,
            }
        )
    return rows


def _line(row: dict[str, Any], rank: int) -> str:
    return (
        f"{rank}. {row['matchup']} | pick {row['winner_pick']} "
        f"{_pct(row['winner_probability'])} | "
        f"{row['away_team']} {_pct(row['away_win_pct'])} / "
        f"{row['home_team']} {_pct(row['home_win_pct'])} | "
        f"gap {row['confidence_gap_pct']:.1f} {row['confidence_band']} | "
        f"proj {row['projected_away_score']}-{row['projected_home_score']} "
        f"(T {row['projected_total']})"
    )


def _chunk_messages(header: str, lines: list[str]) -> list[str]:
    messages: list[str] = []
    current = header
    for line in lines:
        addition = f"\n{line}"
        if len(current) + len(addition) > DISCORD_MESSAGE_LIMIT:
            messages.append(current)
            current = f"{header}\n{line}"
        else:
            current += addition
    messages.append(current)
    return messages


def send_daily_prediction_digest(
    db: Session,
    *,
    target_date: date,
    run_stage: str = "daily_open",
    force: bool = False,
) -> dict[str, Any]:
    report_name = _report_name(run_stage)
    if not force and get_report_snapshot(db, report_name, report_date=target_date, max_age_seconds=None):
        return {"sent": 0, "skipped": 1, "reason": "already_sent", "report_name": report_name}

    rows = _build_rows(db, target_date=target_date, run_stage=run_stage)
    if not rows:
        return {
            "sent": 0,
            "skipped": 1,
            "reason": "no_predictions",
            "report_name": report_name,
            "date": target_date.isoformat(),
        }

    sources = {row["probability_source"] for row in rows}
    source = next(iter(sources)) if len(sources) == 1 else "mixed"
    header = (
        f"**MLB ALL-GAMES PREDICTION BOARD**\n"
        f"Date: `{target_date.isoformat()}` | Stage: `{run_stage}` | Games: **{len(rows)}**\n"
        f"Probabilities: `{source}` | Tracking: postgame review grades winner + projected total."
    )
    lines = [_line(row, idx) for idx, row in enumerate(rows, start=1)]
    messages = _chunk_messages(header, lines)

    errors: list[str] = []
    for message in messages:
        ok, error = send_alert_message(message)
        if not ok:
            errors.append(error or "unknown notification error")

    payload = {
        "status": "sent" if not errors else "failed",
        "date": target_date.isoformat(),
        "run_stage": run_stage,
        "count": len(rows),
        "chunks": len(messages),
        "rows": rows,
        "errors": errors,
    }
    if errors:
        return {
            "sent": 0,
            "failed": len(errors),
            "skipped": 0,
            "report_name": report_name,
            "errors": errors,
        }

    store_report_snapshot(db, report_name, payload, report_date=target_date, runtime_ms=None)
    return {
        "sent": len(messages),
        "skipped": 0,
        "failed": 0,
        "games": len(rows),
        "report_name": report_name,
    }
