from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.schema import GameOdds
from app.services.betmgm_public_odds_service import BETMGM_PUBLIC_SPORTSBOOK
from app.services.odds_service import get_odds_provider_pause
from app.services.report_snapshot_service import (
    BETMGM_PUBLIC_VALIDATION_REPORT,
    BETMGM_PUBLIC_VALIDATION_TTL_SECONDS,
    get_report_snapshot,
)


MARKET_SOURCES_REPORT = "market_sources"
ET = ZoneInfo("America/New_York")


def build_market_source_report(db: Session, *, report_date: date | None = None) -> dict[str, Any]:
    """Describe the available odds sources without allowing fallback data into decisions."""
    from app.routes.debug import build_odds_quota_report

    quota = build_odds_quota_report(db)
    validation = get_report_snapshot(
        db,
        BETMGM_PUBLIC_VALIDATION_REPORT,
        report_date=report_date or datetime.now(ET).date(),
        max_age_seconds=BETMGM_PUBLIC_VALIDATION_TTL_SECONDS,
    )
    source_counts = dict(
        db.query(GameOdds.sportsbook, func.count(GameOdds.id))
        .group_by(GameOdds.sportsbook)
        .all()
    )
    primary_observations = sum(
        count for source, count in source_counts.items()
        if source != BETMGM_PUBLIC_SPORTSBOOK
    )
    validation_counts = (validation or {}).get("counts") or {}
    checked = int((validation or {}).get("events_checked") or 0)
    matched = int(validation_counts.get("MATCH") or 0)
    match_rate = round(matched / checked, 4) if checked else None
    provider_blocked = get_odds_provider_pause(db) is not None
    provider_status = str(quota.get("provider_status") or "OK")
    if not provider_blocked and provider_status == "OUT_OF_USAGE_CREDITS":
        provider_status = "OK"

    betmgm_status = "UNAVAILABLE"
    if validation:
        betmgm_status = "VALIDATED" if matched else "SCOUTING"

    return {
        "status": "ok",
        "sources": [
            {
                "id": "the_odds_api",
                "label": "The Odds API",
                "role": "PRIMARY",
                "decision_eligible": True,
                "status": "PAUSED" if provider_blocked else provider_status,
                "detail": quota.get("latest_provider_message") or quota.get("quota_alert") or "Primary decision source",
                "observations": primary_observations,
                "month_used": quota.get("month_used"),
                "month_remaining": quota.get("month_remaining"),
            },
            {
                "id": BETMGM_PUBLIC_SPORTSBOOK,
                "label": "BetMGM Public",
                "role": "RESEARCH ONLY",
                "decision_eligible": False,
                "status": betmgm_status,
                "detail": "Public-page validation only. It never enters edge, CLV, or trade calculations.",
                "observations": int(source_counts.get(BETMGM_PUBLIC_SPORTSBOOK, 0)),
                "events_checked": checked,
                "matched": matched,
                "match_rate": match_rate,
                "validated_at": (validation or {}).get("validated_at"),
                "validation_snapshot": (validation or {}).get("snapshot"),
            },
        ],
        "stored_observations_by_source": dict(sorted(source_counts.items())),
        "routing": {
            "decision_source": "the_odds_api",
            "fallback_mode": "research_only",
            "guardrail": (
                "Public BetMGM observations are isolated from GameOdds and cannot affect edge, CLV, "
                "ranking, alerts, or trade decisions."
            ),
        },
    }
