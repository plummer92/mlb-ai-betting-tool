from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import ODDS_API_MONTHLY_QUOTA
from app.models.schema import OddsApiRequestLog, TradableDecisionSnapshot
from app.routes.ranked import _build_decision_queue
from app.services.notification_service import send_alert_message

ET = ZoneInfo("America/New_York")


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    return datetime.now(ET).date()


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _find_existing_snapshot(db: Session, row: dict) -> TradableDecisionSnapshot | None:
    game_date = _as_date(row.get("game_date"))
    query = db.query(TradableDecisionSnapshot).filter(
        TradableDecisionSnapshot.game_date == game_date,
        TradableDecisionSnapshot.game_id == row.get("game_id"),
    )
    edge_result_id = row.get("edge_result_id")
    if edge_result_id is None:
        query = query.filter(TradableDecisionSnapshot.edge_result_id.is_(None))
    else:
        query = query.filter(TradableDecisionSnapshot.edge_result_id == edge_result_id)
    return query.one_or_none()


def _apply_snapshot_row(snapshot: TradableDecisionSnapshot, row: dict) -> None:
    snapshot.game_id = row["game_id"]
    snapshot.edge_result_id = row.get("edge_result_id")
    snapshot.game_date = _as_date(row.get("game_date"))
    snapshot.matchup = row.get("matchup") or row.get("game")
    snapshot.play = row.get("play")
    snapshot.decision_status = row.get("decision_status") or "UNKNOWN"
    snapshot.tradable_signal = row.get("tradable_signal") or "UNKNOWN"
    snapshot.tradable_reason = row.get("tradable_reason")
    snapshot.decision_reason = row.get("decision_reason")
    snapshot.raw_edge_pct = _decimal(row.get("raw_edge_pct"))
    snapshot.adjusted_edge_pct = _decimal(row.get("adjusted_edge_pct"))
    snapshot.market_respect_score = row.get("market_trust_score")
    snapshot.market_respect_tag = row.get("market_respect_tag")
    snapshot.odds_freshness_status = row.get("odds_freshness_status")
    snapshot.policy_status = row.get("policy_status")
    snapshot.policy_score = row.get("totals_policy_score")
    snapshot.trade_allowed = bool(row.get("trade_allowed"))
    snapshot.snapshot_json = json.dumps(row, default=_json_default, sort_keys=True)


def persist_tradable_decisions(db: Session, limit: int = 50, active_only: bool = True) -> dict:
    rows = _build_decision_queue(db=db, limit=limit, active_only=active_only)
    created = 0
    updated = 0

    for row in rows:
        if not row.get("game_id"):
            continue
        snapshot = _find_existing_snapshot(db, row)
        if snapshot is None:
            snapshot = TradableDecisionSnapshot(
                game_id=row["game_id"],
                game_date=_as_date(row.get("game_date")),
                decision_status=row.get("decision_status") or "UNKNOWN",
                tradable_signal=row.get("tradable_signal") or "UNKNOWN",
            )
            db.add(snapshot)
            created += 1
        else:
            updated += 1
        _apply_snapshot_row(snapshot, row)

    db.commit()

    return {
        "status": "ok",
        "created": created,
        "updated": updated,
        "total": len(rows),
        "decision_counts": dict(Counter(row.get("decision_status") or "UNKNOWN" for row in rows)),
        "tradable_signal_counts": dict(Counter(row.get("tradable_signal") or "UNKNOWN" for row in rows)),
        "rows": rows,
    }


def _odds_quota_summary(db: Session) -> dict:
    now = datetime.now(ET)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = (
        db.query(OddsApiRequestLog)
        .filter(
            OddsApiRequestLog.requested_at >= month_start,
            OddsApiRequestLog.status == "ok",
        )
        .count()
    )
    return {
        "quota": ODDS_API_MONTHLY_QUOTA,
        "used": used,
        "remaining": max(ODDS_API_MONTHLY_QUOTA - used, 0),
    }


def build_daily_trade_summary(db: Session, limit: int = 50, active_only: bool = True) -> dict:
    journal = persist_tradable_decisions(db=db, limit=limit, active_only=active_only)
    rows = journal["rows"]
    trades = [row for row in rows if row.get("tradable_signal") == "TRADE" and row.get("trade_allowed")]
    watch = [row for row in rows if row.get("tradable_signal") == "WATCH"]
    passes = [row for row in rows if row.get("tradable_signal") == "PASS"]
    blocked = [row for row in rows if row.get("decision_status") == "BLOCKED"]
    quota = _odds_quota_summary(db)
    top_blockers = Counter(
        (row.get("tradable_reason") or row.get("policy_status") or "unknown").lower()
        for row in rows
        if row.get("tradable_signal") != "TRADE"
    )

    title = "MLB Trade Summary" if trades else "MLB No-Trade Summary"
    lines = [
        f"**{title}**",
        f"TRADE {len(trades)} | WATCH {len(watch)} | PASS {len(passes)} | BLOCKED {len(blocked)}",
        f"Odds quota: {quota['used']}/{quota['quota']} used, {quota['remaining']} left",
    ]

    if trades:
        lines.append("Live trade candidates:")
        for row in trades[:10]:
            lines.append(
                f"#{row.get('rank')} {row.get('matchup')} | {row.get('play')} | "
                f"edge={float(row.get('adjusted_edge_pct') or 0) * 100:.1f}% | "
                f"MRS={row.get('market_trust_score')} {row.get('market_respect_tag')}"
            )
    else:
        lines.append("No live TRADE plays. Research-only WATCH rows stay off the bet card until CLV or market agreement shows up.")
        if top_blockers:
            reason, count = top_blockers.most_common(1)[0]
            lines.append(f"Top blocker: {reason} ({count})")
        for row in watch[:5]:
            lines.append(
                f"WATCH #{row.get('rank')} {row.get('matchup')} | {row.get('play')} | "
                f"edge={float(row.get('adjusted_edge_pct') or 0) * 100:.1f}% | {row.get('tradable_reason')}"
            )

    return {
        "status": "ok",
        "message": "\n".join(lines),
        "sentiment": "trade" if trades else "no_trade",
        "quota": quota,
        "journal": {key: value for key, value in journal.items() if key != "rows"},
    }


def send_daily_trade_summary(db: Session, limit: int = 50, active_only: bool = True) -> dict:
    summary = build_daily_trade_summary(db=db, limit=limit, active_only=active_only)
    ok, error = send_alert_message(summary["message"])
    return {
        "sent": 1 if ok and not error else 0,
        "status": "ok" if ok else "error",
        "error": error,
        "summary": summary,
    }
