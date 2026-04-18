from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from scipy.stats import norm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import ALERT_DESTINATION
from app.models.schema import BetAlert, EdgeResult, Game, Prediction, GameOdds
from app.services.betting_policy import qualifies_for_bet_policy
from app.services.edge_service import get_trustworthy_active_edges, TOTAL_STD_DEV
from app.services.notification_service import send_alert_message
from app.services.odds_service import is_odds_snapshot_fresh

ET = ZoneInfo("America/New_York")


def get_sniper_confidence(edge: EdgeResult) -> float:
    """Calculate the probability of the recommended play as a percentage."""
    play = edge.recommended_play
    if not play:
        return 0.0

    if play in ["away_ml", "home_ml"]:
        prob = float(edge.model_away_win_pct if play == "away_ml" else edge.model_home_win_pct)
        return prob * 100.0
    
    if play in ["over", "under"]:
        model_total = float(edge.model_total or 0)
        book_total = float(edge.book_total or 0)
        if not model_total or not book_total:
            return 0.0
        
        # Normal distribution approximation for over/under probability
        prob_over = float(1 - norm.cdf(book_total, loc=model_total, scale=TOTAL_STD_DEV))
        prob = prob_over if play == "over" else (1 - prob_over)
        return prob * 100.0
        
    return 0.0


def get_average_odds(db: Session, game_id: int, play: str) -> str:
    """Calculate average odds across all fresh snapshots for a game."""
    rows = db.query(GameOdds).filter(GameOdds.game_id == game_id).all()
    fresh_rows = [r for r in rows if is_odds_snapshot_fresh(r)]
    
    if not fresh_rows:
        return "N/A"
        
    if play in ["away_ml", "home_ml"]:
        vals = [r.away_ml if play == "away_ml" else r.home_ml for r in fresh_rows if (r.away_ml and r.home_ml)]
        if not vals:
            return "N/A"
        avg = sum(vals) / len(vals)
        return f"{avg:+.0f}"
    
    if play in ["over", "under"]:
        lines = [float(r.total_line) for r in fresh_rows if r.total_line]
        if not lines:
            return "N/A"
        avg_line = sum(lines) / len(lines)
        odds_vals = [r.over_odds if play == "over" else r.under_odds for r in fresh_rows if (r.over_odds and r.under_odds)]
        if not odds_vals:
            return f"{avg_line:.1f}"
        avg_odds = sum(odds_vals) / len(odds_vals)
        return f"{avg_line:.1f} ({avg_odds:+.0f})"
        
    return "N/A"


def qualifies_for_alert(edge: EdgeResult) -> bool:
    """
    Sniper Alert Criteria:
    - Totals: Confidence >= 70.0%
    - Moneyline: Confidence >= 80.0%
    """
    play = edge.recommended_play
    if not play:
        return False

    ev = 0.0
    if play == "away_ml":
        ev = float(edge.ev_away or 0)
    elif play == "home_ml":
        ev = float(edge.ev_home or 0)
    elif play == "under":
        ev = float(edge.ev_under or 0)
    elif play == "over":
        ev = float(edge.ev_over or 0)

    if not qualifies_for_bet_policy(
        play=play,
        edge_pct=float(edge.edge_pct or 0),
        ev=ev,
        confidence=edge.confidence_tier,
    ):
        return False

    confidence = get_sniper_confidence(edge)
    
    if play in ["over", "under"]:
        return confidence >= 70.0
    
    if play in ["away_ml", "home_ml"]:
        return confidence >= 80.0
        
    return False


def build_sniper_alert_message(game: Game, edge: EdgeResult, db: Session) -> str:
    """Format a clean, actionable Discord message."""
    away = game.away_team
    home = game.home_team
    play = edge.recommended_play
    
    # Format Recommended Pick
    if play == "over":
        pick = f"OVER {edge.book_total}"
    elif play == "under":
        pick = f"UNDER {edge.book_total}"
    elif play == "away_ml":
        pick = f"Moneyline: {away}"
    elif play == "home_ml":
        pick = f"Moneyline: {home}"
    else:
        pick = str(play)

    confidence = get_sniper_confidence(edge)
    avg_odds = get_average_odds(db, game.game_id, play or "")

    # Format odds line: "Line: X.X | Odds: -YYY" for totals, "Odds: +YYY" for ML
    if play in ["over", "under"] and " (" in avg_odds:
        line_val, odds_val = avg_odds.split(" (", 1)
        odds_val = odds_val.rstrip(")")
        odds_field = f"**Line:** {line_val} | **Odds:** {odds_val}"
    else:
        odds_field = f"**Odds:** {avg_odds}"

    return (
        f"🎯 **SNIPER ALERT** 🎯\n\n"
        f"**Game:** {away} @ {home}\n"
        f"**Recommended Pick:** {pick}\n"
        f"**Confidence:** {confidence:.1f}%\n"
        f"{odds_field}"
    )


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
    print(f"[alerts] Evaluating {len(edges)} edges for {today} for Sniper Alerts", flush=True)

    created = 0
    sent = 0
    skipped = 0
    failed = 0
    qualified = 0

    for edge, game, prediction in edges:
        # Dedupe on game_id and prediction_id
        existing = (
            db.query(BetAlert)
            .filter(
                BetAlert.game_id == game.game_id, 
                BetAlert.prediction_id == prediction.prediction_id
            )
            .first()
        )
        if existing:
            skipped += 1
            continue

        if not qualifies_for_alert(edge):
            skipped += 1
            continue

        qualified += 1
        message = build_sniper_alert_message(game, edge, db)
        
        # We still store synopsis and rationale for compatibility, but send the sniper message
        from app.services.synopsis_service import build_edge_synopsis
        synopsis, rationale = build_edge_synopsis(game, edge)
        
        ev = 0.0
        if edge.recommended_play == "away_ml": ev = float(edge.ev_away or 0)
        elif edge.recommended_play == "home_ml": ev = float(edge.ev_home or 0)
        elif edge.recommended_play == "under": ev = float(edge.ev_under or 0)
        elif edge.recommended_play == "over": ev = float(edge.ev_over or 0)

        alert = BetAlert(
            game_id=game.game_id,
            prediction_id=prediction.prediction_id,
            edge_result_id=edge.id,
            game_date=game.game_date,
            play=edge.recommended_play or "none",
            edge_pct=edge.edge_pct,
            ev=ev,
            confidence=edge.confidence_tier or "sniper",
            synopsis=message,  # Use the sniper message as synopsis
            rationale_json=json.dumps(rationale),
            sent_to=ALERT_DESTINATION,
            status="pending",
        )
        db.add(alert)
        db.flush()
        created += 1

        ok, error = send_alert_message(message)
        if ok:
            alert.status = "sent"
            sent += 1
        else:
            alert.status = "failed"
            alert.error_message = error
            failed += 1

    db.commit()
    print(
        f"[alerts] Sniper Done: {len(edges)} evaluated, {qualified} qualified, "
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
    """Send a sniper alert for a single game only."""
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

    # Dedupe: skip if this game/prediction combo was already alerted
    already = (
        db.query(BetAlert)
        .filter(
            BetAlert.game_id == game_id,
            BetAlert.prediction_id == prediction.prediction_id,
        )
        .first()
    )
    if already:
        return {"created": 0, "sent": 0, "skipped": 1, "reason": "already alerted for this prediction"}

    if not qualifies_for_alert(edge):
        return {"created": 0, "sent": 0, "skipped": 1, "reason": "does not meet sniper criteria"}

    message = build_sniper_alert_message(game, edge, db)
    from app.services.synopsis_service import build_edge_synopsis
    _syn, rationale = build_edge_synopsis(game, edge)
    
    ev = 0.0
    if edge.recommended_play == "away_ml": ev = float(edge.ev_away or 0)
    elif edge.recommended_play == "home_ml": ev = float(edge.ev_home or 0)
    elif edge.recommended_play == "under": ev = float(edge.ev_under or 0)
    elif edge.recommended_play == "over": ev = float(edge.ev_over or 0)

    alert = BetAlert(
        game_id=game.game_id,
        prediction_id=prediction.prediction_id,
        edge_result_id=edge.id,
        game_date=game.game_date,
        play=edge.recommended_play or "none",
        edge_pct=edge.edge_pct,
        ev=ev,
        confidence=edge.confidence_tier or "sniper",
        synopsis=message,
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

    ok, error = send_alert_message(message)
    if ok:
        alert.status = "sent"
        db.commit()
        return {"created": 1, "sent": 1, "skipped": 0}
    else:
        alert.status = "failed"
        alert.error_message = error
        db.commit()
        return {"created": 1, "sent": 0, "skipped": 0, "failed": 1, "error": error}
