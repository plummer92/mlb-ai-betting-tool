from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.schema import Game, GameOdds, SnapshotType
from app.routes.ranked import _build_decision_queue
from app.services.notification_service import send_alert_message
from app.services.odds_service import get_latest_odds_snapshot
from app.services.report_snapshot_service import get_report_snapshot, store_report_snapshot


REMINDER_REPORT_PREFIX = "pregame_bet_reminder"
BETTABLE_DECISION_STATUSES = {"FIRE"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _reminder_report_name(game_id: int) -> str:
    return f"{REMINDER_REPORT_PREFIX}_{game_id}"


def _already_sent(db: Session, *, game_id: int, game_date: date | None) -> bool:
    return (
        get_report_snapshot(
            db,
            _reminder_report_name(game_id),
            report_date=game_date,
            max_age_seconds=None,
        )
        is not None
    )


def _latest_preferred_odds(db: Session, game_id: int) -> GameOdds | None:
    return (
        get_latest_odds_snapshot(db, game_id=game_id, snapshot_type=SnapshotType.pregame)
        or get_latest_odds_snapshot(db, game_id=game_id, snapshot_type=SnapshotType.open)
    )


def _price_for_play(play: str | None, odds: GameOdds | None) -> int | None:
    if odds is None:
        return None
    if play == "away_ml":
        return odds.away_ml
    if play == "home_ml":
        return odds.home_ml
    if play == "over":
        return odds.over_odds
    if play == "under":
        return odds.under_odds
    return None


def _format_price(value: int | None) -> str:
    if value is None:
        return "---"
    return f"{value:+d}"


def _format_pick(row: dict[str, Any], game: Game, odds: GameOdds | None) -> str:
    play = (row.get("play") or "").lower()
    price = _format_price(_price_for_play(play, odds))
    if play == "away_ml":
        return f"{game.away_team} ML ({price})"
    if play == "home_ml":
        return f"{game.home_team} ML ({price})"
    if play in {"over", "under"}:
        line = (
            float(odds.total_line)
            if odds is not None and odds.total_line is not None
            else (row.get("totals_policy") or {}).get("components", {}).get("market_total")
        )
        line_text = f"{line:g}" if isinstance(line, (float, int)) else "---"
        return f"{play.upper()} {line_text} ({price})"
    return (play or "unknown").upper()


def _bettable_row(row: dict[str, Any]) -> bool:
    return (
        row.get("decision_status") in BETTABLE_DECISION_STATUSES
        and row.get("tradable_signal") == "TRADE"
        and bool(row.get("trade_allowed"))
        and float(row.get("adjusted_edge_pct") or 0) > 0
    )


def _build_message(row: dict[str, Any], game: Game, odds: GameOdds | None, *, minutes_before: int) -> str:
    pick = _format_pick(row, game, odds)
    edge = float(row.get("adjusted_edge_pct") or row.get("raw_edge_pct") or 0)
    market_score = row.get("market_trust_score")
    policy_score = row.get("totals_policy_score")
    tags = ", ".join((row.get("market_respect_tags") or [])[:2]) or "MARKET NEUTRAL"
    book = row.get("sportsbook") or (odds.sportsbook if odds else "book")
    start = row.get("start_time") or game.start_time or "---"
    reason = row.get("decision_reason") or row.get("tradable_reason") or "Trade gates passed."

    return (
        f"**5-MIN MLB BET ALERT**\n"
        f"Game: **{game.away_team} @ {game.home_team}**\n"
        f"Bet: **{pick}**\n"
        f"Book/source: `{book}` | First pitch: `{start}`\n"
        f"Adjusted edge: **{edge * 100:.1f}%** | Market score: **{market_score}** | Policy score: **{policy_score}**\n"
        f"Market: `{tags}`\n"
        f"Window: about **{minutes_before} minutes** before first pitch.\n"
        f"Why: {reason}"
    )


def send_pregame_bet_reminder_for_game(
    db: Session,
    *,
    game_id: int,
    minutes_before: int = 5,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    now_utc = now_utc or _utcnow()
    game = db.query(Game).filter(Game.game_id == game_id).first()
    if game is None:
        return {"sent": 0, "skipped": 1, "reason": "game_not_found", "game_id": game_id}

    start_utc = _parse_datetime(game.start_time)
    if start_utc is None:
        return {"sent": 0, "skipped": 1, "reason": "missing_start_time", "game_id": game_id}
    if now_utc >= start_utc:
        return {"sent": 0, "skipped": 1, "reason": "past_first_pitch", "game_id": game_id}
    if _already_sent(db, game_id=game_id, game_date=game.game_date):
        return {"sent": 0, "skipped": 1, "reason": "already_sent", "game_id": game_id}

    rows = _build_decision_queue(db=db, limit=50, active_only=True)
    row = next((candidate for candidate in rows if candidate.get("game_id") == game_id), None)
    if row is None:
        return {"sent": 0, "skipped": 1, "reason": "no_decision_row", "game_id": game_id}
    if not _bettable_row(row):
        return {
            "sent": 0,
            "skipped": 1,
            "reason": "not_bettable",
            "game_id": game_id,
            "decision_status": row.get("decision_status"),
            "tradable_signal": row.get("tradable_signal"),
            "tradable_reason": row.get("tradable_reason"),
        }

    odds = _latest_preferred_odds(db, game_id)
    message = _build_message(row, game, odds, minutes_before=minutes_before)
    ok, error = send_alert_message(message)
    payload = {
        "status": "sent" if ok else "failed",
        "game_id": game_id,
        "matchup": f"{game.away_team} @ {game.home_team}",
        "play": row.get("play"),
        "decision_status": row.get("decision_status"),
        "tradable_signal": row.get("tradable_signal"),
        "adjusted_edge_pct": row.get("adjusted_edge_pct"),
        "market_trust_score": row.get("market_trust_score"),
        "totals_policy_score": row.get("totals_policy_score"),
        "message": message,
        "error": error,
    }
    if ok:
        store_report_snapshot(
            db,
            _reminder_report_name(game_id),
            payload,
            report_date=game.game_date,
            runtime_ms=None,
        )
        return {"sent": 1, "skipped": 0, "game_id": game_id, "play": row.get("play")}
    return {"sent": 0, "skipped": 0, "failed": 1, "game_id": game_id, "error": error}
