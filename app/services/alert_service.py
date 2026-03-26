from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import ALERT_CONFIDENCE_LEVELS, ALERT_DESTINATION, ALERT_MIN_EDGE, ALERT_MIN_EV
from app.models.schema import BetAlert, EdgeResult, Game, Prediction
from app.services.notification_service import send_alert_message
from app.services.synopsis_service import build_edge_synopsis

ET = ZoneInfo("America/New_York")


def _best_ev_for_play(edge: EdgeResult) -> float:
    play = edge.recommended_play
    if play == "away_ml":
        return float(edge.ev_away or 0)
    if play == "home_ml":
        return float(edge.ev_home or 0)
    if play == "under":
        return float(edge.ev_under or 0)
    if play == "over":
        return float(edge.ev_over or 0)
    return 0.0


def qualifies_for_alert(edge: EdgeResult) -> bool:
    if not edge.recommended_play:
        return False
    if (edge.confidence_tier or "").lower() not in ALERT_CONFIDENCE_LEVELS:
        return False
    if float(edge.edge_pct or 0) < ALERT_MIN_EDGE:
        return False
    if _best_ev_for_play(edge) < ALERT_MIN_EV:
        return False
    return True


def create_and_send_alerts_for_today(db: Session) -> dict:
    today = datetime.now(ET).date()

    rows = (
        db.query(EdgeResult, Game, Prediction)
        .join(Game, Game.game_id == EdgeResult.game_id)
        .join(Prediction, Prediction.prediction_id == EdgeResult.prediction_id)
        .filter(Game.game_date == today)
        .order_by(Game.game_id, EdgeResult.calculated_at.desc())
        .all()
    )

    # keep only latest edge per game
    latest_by_game = {}
    for edge, game, prediction in rows:
        if game.game_id not in latest_by_game:
            latest_by_game[game.game_id] = (edge, game, prediction)

    edges = list(latest_by_game.values())

    created = 0
    sent = 0
    skipped = 0
    failed = 0

    for edge, game, prediction in edges:

        existing = (
            db.query(BetAlert)
            .filter(BetAlert.game_id == game.game_id, BetAlert.game_date == today)
            .first()
        )
        if existing:
            skipped += 1
            continue

        if not qualifies_for_alert(edge):
            skipped += 1
            continue

        synopsis, rationale = build_edge_synopsis(game, edge)
        ev = _best_ev_for_play(edge)

        alert = BetAlert(
            game_id=game.game_id,
            prediction_id=prediction.prediction_id,
            edge_result_id=edge.id,
            game_date=game.game_date,
            play=edge.recommended_play,
            edge_pct=edge.edge_pct,
            ev=ev,
            confidence=edge.confidence_tier,
            synopsis=synopsis,
            rationale_json=json.dumps(rationale),
            sent_to=ALERT_DESTINATION,
            status="pending",
        )
        db.add(alert)
        db.flush()
        created += 1

        ok, error = send_alert_message(synopsis)
        if ok:
            alert.status = "sent"
            sent += 1
        else:
            alert.status = "failed"
            alert.error_message = error
            failed += 1

    db.commit()

    return {
        "created": created,
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
    }
