from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from time import perf_counter
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.schema import GameOutcomeReview, ReportSnapshot

logger = logging.getLogger(__name__)


DASHBOARD_REPORT_TTL_SECONDS = 60 * 60 * 12


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_report_snapshot(
    db: Session,
    report_name: str,
    *,
    report_date: date | None = None,
    max_age_seconds: int | None = DASHBOARD_REPORT_TTL_SECONDS,
) -> dict | None:
    row = (
        db.query(ReportSnapshot)
        .filter(
            ReportSnapshot.report_name == report_name,
            ReportSnapshot.report_date == report_date,
            ReportSnapshot.status == "ok",
        )
        .order_by(ReportSnapshot.generated_at.desc(), ReportSnapshot.id.desc())
        .first()
    )
    if row is None:
        return None
    if max_age_seconds is not None and row.generated_at is not None:
        generated_at = row.generated_at
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
        if generated_at < _utcnow() - timedelta(seconds=max_age_seconds):
            return None
    try:
        payload = json.loads(row.payload_json)
    except json.JSONDecodeError:
        logger.warning("[report-snapshot] invalid json report=%s id=%s", report_name, row.id)
        return None
    if isinstance(payload, dict):
        payload.setdefault("snapshot", {})
        payload["snapshot"].update({
            "report_name": report_name,
            "report_date": row.report_date.isoformat() if row.report_date else None,
            "generated_at": row.generated_at.isoformat() if row.generated_at else None,
            "runtime_ms": row.runtime_ms,
        })
    return payload


def store_report_snapshot(
    db: Session,
    report_name: str,
    payload: dict,
    *,
    report_date: date | None = None,
    runtime_ms: int | None = None,
) -> ReportSnapshot:
    row = (
        db.query(ReportSnapshot)
        .filter(
            ReportSnapshot.report_name == report_name,
            ReportSnapshot.report_date == report_date,
        )
        .first()
    )
    if row is None:
        row = ReportSnapshot(report_name=report_name, report_date=report_date)
        db.add(row)
    row.generated_at = _utcnow()
    row.status = "ok"
    row.runtime_ms = runtime_ms
    row.payload_json = json.dumps(payload, default=str, separators=(",", ":"))
    row.error = None
    db.commit()
    db.refresh(row)
    return row


def build_and_store_report_snapshot(
    db: Session,
    report_name: str,
    builder: Callable[[], dict],
    *,
    report_date: date | None = None,
) -> dict:
    started = perf_counter()
    payload = builder()
    runtime_ms = int((perf_counter() - started) * 1000)
    store_report_snapshot(db, report_name, payload, report_date=report_date, runtime_ms=runtime_ms)
    return {"report_name": report_name, "runtime_ms": runtime_ms, "status": "ok"}


def build_performance_summary(db: Session) -> dict:
    total = db.query(GameOutcomeReview).count()
    model_correct = db.query(GameOutcomeReview).filter(GameOutcomeReview.was_model_correct.is_(True)).count()
    wins = db.query(GameOutcomeReview).filter(GameOutcomeReview.bet_result == "win").count()
    losses = db.query(GameOutcomeReview).filter(GameOutcomeReview.bet_result == "loss").count()
    pushes = db.query(GameOutcomeReview).filter(GameOutcomeReview.bet_result == "push").count()
    no_bet = db.query(GameOutcomeReview).filter(GameOutcomeReview.bet_result == "no_bet").count()
    decisions = wins + losses
    bets_graded = wins + losses + pushes
    profit = wins * (100 / 110) - losses
    return {
        "status": "ok",
        "total_predictions": total,
        "model_directional_accuracy": round(model_correct / total, 4) if total else None,
        "bets_graded": bets_graded,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "no_bet": no_bet,
        "win_rate": round(wins / decisions, 4) if decisions else None,
        "roi_flat_110": round(profit / bets_graded, 4) if bets_graded else None,
    }


def refresh_dashboard_report_snapshots(db: Session, *, report_date: date | None = None) -> dict:
    from app.routes.debug import build_odds_warehouse_report, build_totals_policy_report
    from app.routes.bullpen import _build_bullpen_today
    from app.services.market_audit_service import get_clv_report, get_movement_backtest_report

    target_date = report_date or date.today()
    builders: dict[str, Callable[[], dict]] = {
        "odds_warehouse": lambda: build_odds_warehouse_report(db),
        "totals_policy": lambda: build_totals_policy_report(db, min_sample=5),
        "paper_clv": lambda: get_clv_report(db, limit=25),
        "movement_report": lambda: get_movement_backtest_report(db, min_sample=1, limit=50),
        "bullpen_today": lambda: {"status": "ok", "reports": _build_bullpen_today(db, target_date)},
        "performance_summary": lambda: build_performance_summary(db),
    }

    results = []
    failures = []
    for name, builder in builders.items():
        try:
            results.append(
                build_and_store_report_snapshot(db, name, builder, report_date=target_date)
            )
        except Exception as exc:
            db.rollback()
            logger.exception("[report-snapshot] refresh failed report=%s", name)
            failures.append({"report_name": name, "error": str(exc)})
    return {
        "status": "ok" if not failures else "partial",
        "date": target_date.isoformat(),
        "created": len(results),
        "failed": len(failures),
        "reports": results,
        "failures": failures,
    }


def refresh_decision_snapshots(db: Session, *, report_date: date | None = None) -> dict:
    from app.routes.ranked import _build_decision_queue, _build_ranked_rows

    target_date = report_date or date.today()
    builders: dict[str, Callable[[], dict]] = {
        "decision_queue": lambda: {
            "status": "ok",
            "rows": _build_decision_queue(db=db, limit=50, active_only=True),
            "active_only": True,
        },
        "ranked_rows": lambda: {
            "status": "ok",
            "rows": _build_ranked_rows(db=db, limit=50, active_only=True),
            "active_only": True,
        },
    }

    results = []
    failures = []
    for name, builder in builders.items():
        try:
            results.append(
                build_and_store_report_snapshot(db, name, builder, report_date=target_date)
            )
        except Exception as exc:
            db.rollback()
            logger.exception("[report-snapshot] decision refresh failed report=%s", name)
            failures.append({"report_name": name, "error": str(exc)})
    return {
        "status": "ok" if not failures else "partial",
        "date": target_date.isoformat(),
        "created": len(results),
        "failed": len(failures),
        "reports": results,
        "failures": failures,
    }


def refresh_decision_snapshots_for_date(*, report_date: date | None = None) -> dict:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        return refresh_decision_snapshots(db, report_date=report_date)
    finally:
        db.close()
