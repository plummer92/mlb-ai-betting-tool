from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import ALERT_CONFIDENCE_LEVELS, ALERT_DESTINATION, ALERT_MIN_EDGE, ALERT_MIN_EV
from app.models.schema import BetAlert, EdgeResult, Game, Prediction
from app.services.edge_service import get_trustworthy_active_edges
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
        print(f"[alerts]   FAIL no_play: game={edge.game_id}", flush=True)
        return False

    confidence = (edge.confidence_tier or "").lower()
    edge_val = float(edge.edge_pct or 0)
    ev_val = _best_ev_for_play(edge)

    # Strong plays with meaningful edge always qualify regardless of EV threshold
    if confidence == "strong" and edge_val > 0.10:
        return True

    if confidence not in ALERT_CONFIDENCE_LEVELS:
        print(
            f"[alerts]   FAIL confidence: game={edge.game_id} confidence={confidence!r} "
            f"not in {ALERT_CONFIDENCE_LEVELS}",
            flush=True,
        )
        return False
    if edge_val < ALERT_MIN_EDGE:
        print(
            f"[alerts]   FAIL edge: game={edge.game_id} edge_pct={edge_val:.4f} < {ALERT_MIN_EDGE}",
            flush=True,
        )
        return False
    if ev_val < ALERT_MIN_EV:
        print(
            f"[alerts]   FAIL ev: game={edge.game_id} ev={ev_val:.4f} < {ALERT_MIN_EV} "
            f"(play={edge.recommended_play})",
            flush=True,
        )
        return False
    return True


def create_and_send_alerts_for_today(db: Session) -> dict:
    today = datetime.now(ET).date()
    trusted_rows = get_trustworthy_active_edges(db, game_date=today)
    rows = [(edge, game, prediction) for edge, game, prediction, _odds in trusted_rows]

    # keep only latest edge per game
    latest_by_game = {}
    for edge, game, prediction in rows:
        if game.game_id not in latest_by_game:
            latest_by_game[game.game_id] = (edge, game, prediction)

    edges = list(latest_by_game.values())
    print(f"[alerts] Evaluating {len(edges)} edges for {today}", flush=True)

    created = 0
    sent = 0
    skipped = 0
    failed = 0
    qualified = 0

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
            print(
                f"[alerts] Game {game.game_id}: skipped (play={edge.recommended_play}, "
                f"confidence={edge.confidence_tier}, edge={edge.edge_pct}, "
                f"ev={_best_ev_for_play(edge):.4f})",
                flush=True,
            )
            skipped += 1
            continue

        qualified += 1
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
    print(
        f"[alerts] Done: {len(edges)} evaluated, {qualified} qualified, "
        f"{sent} sent, {skipped} skipped, {failed} failed",
        flush=True,
    )

    return {
        "created": created,
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
    }


def create_and_send_alert_for_game(db: Session, game_id: int) -> dict:
    """
    Send an alert for a single game only.
    Dedupes on (game_id, edge_result_id) so morning and pregame alerts can
    both fire (different edge_result_id), but the exact same calculation
    is never sent twice.
    """
    today = datetime.now(ET).date()

    trusted_rows = get_trustworthy_active_edges(db, game_date=today)
    row = next(
        (
            (edge, game, prediction)
            for edge, game, prediction, _odds in trusted_rows
            if game.game_id == game_id
        ),
        None,
    )

    if not row:
        return {"created": 0, "sent": 0, "skipped": 1, "reason": "no edge found for game"}

    edge, game, prediction = row

    # Dedupe: skip if this exact edge_result was already alerted
    already = (
        db.query(BetAlert)
        .filter(
            BetAlert.game_id == game_id,
            BetAlert.edge_result_id == edge.id,
        )
        .first()
    )
    if already:
        return {"created": 0, "sent": 0, "skipped": 1, "reason": "already alerted for this edge_result"}

    if not qualifies_for_alert(edge):
        return {"created": 0, "sent": 0, "skipped": 1, "reason": "does not qualify (ev/edge/confidence)"}

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
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"created": 0, "sent": 0, "skipped": 1, "reason": "duplicate (race condition)"}

    ok, error = send_alert_message(synopsis)
    if ok:
        alert.status = "sent"
        db.commit()
        return {"created": 1, "sent": 1, "skipped": 0}
    else:
        alert.status = "failed"
        alert.error_message = error
        db.commit()
        return {"created": 1, "sent": 0, "skipped": 0, "failed": 1, "error": error}
