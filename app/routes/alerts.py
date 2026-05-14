from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.middleware.auth import verify_api_key
from app.middleware.limiter import limiter
from app.models.schema import BetAlert, EdgeResult, Game, SandboxPredictionV4

from app.services.alert_service import create_and_send_alerts_for_today
from app.services.synopsis_service import build_edge_synopsis

router = APIRouter(prefix="/api/alerts", tags=["alerts"])
ET = ZoneInfo("America/New_York")


@router.post("/run", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def run_alerts(request: Request, db: Session = Depends(get_db)):
    return create_and_send_alerts_for_today(db)


@router.post("/send", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
def send_alerts(request: Request, db: Session = Depends(get_db)):
    result = create_and_send_alerts_for_today(db)
    return {
        "sent": result.get("sent", 0),
        "skipped": result.get("skipped", 0),
        "failed": result.get("failed", 0),
    }


@router.get("/today")
def alerts_today(db: Session = Depends(get_db)):
    today = datetime.now(ET).date()
    rows = db.query(BetAlert).filter(BetAlert.game_date == today).order_by(BetAlert.alert_time.desc()).all()
    return [
        {
            "id": r.id,
            "game_id": r.game_id,
            "play": r.play,
            "edge_pct": float(r.edge_pct),
            "ev": float(r.ev),
            "confidence": r.confidence,
            "status": r.status,
            "synopsis": r.synopsis,
            "bet_result": r.bet_result,
            "alert_time": r.alert_time,
        }
        for r in rows
    ]


@router.get("/commentary/today")
def commentary_today(limit: int = 6, db: Session = Depends(get_db)):
    today = datetime.now(ET).date()

    alert_rows = (
        db.query(BetAlert, Game)
        .join(Game, Game.game_id == BetAlert.game_id)
        .filter(BetAlert.game_date == today)
        .order_by(BetAlert.edge_pct.desc(), BetAlert.alert_time.desc())
        .limit(limit)
        .all()
    )

    alert_items = [
        {
            "game_id": alert.game_id,
            "away_team": game.away_team,
            "home_team": game.home_team,
            "matchup": f"{game.away_team} @ {game.home_team}",
            "play": alert.play,
            "confidence": alert.confidence,
            "edge_pct": float(alert.edge_pct),
            "ev": float(alert.ev),
            "status": alert.status,
            "synopsis": alert.synopsis,
            "alert_time": alert.alert_time,
        }
        for alert, game in alert_rows
    ]

    edge_rows = (
        db.query(EdgeResult, Game)
        .join(Game, Game.game_id == EdgeResult.game_id)
        .filter(
            Game.game_date == today,
            EdgeResult.is_active.is_(True),
            EdgeResult.recommended_play.isnot(None),
        )
        .order_by(EdgeResult.edge_pct.desc(), EdgeResult.calculated_at.desc())
        .all()
    )

    latest_by_game = {}
    for edge, game in edge_rows:
        if edge.game_id not in latest_by_game:
            latest_by_game[edge.game_id] = (edge, game)

    edge_items = []
    for edge, game in list(latest_by_game.values())[:limit]:
        synopsis, _ = build_edge_synopsis(game, edge)
        edge_items.append(
            {
                "game_id": edge.game_id,
                "away_team": game.away_team,
                "home_team": game.home_team,
                "matchup": f"{game.away_team} @ {game.home_team}",
                "play": edge.recommended_play,
                "confidence": edge.confidence_tier,
                "edge_pct": float(edge.edge_pct) if edge.edge_pct is not None else None,
                "ev": None,
                "status": "watch",
                "synopsis": synopsis,
                "alert_time": edge.calculated_at,
            }
        )

    items = alert_items or edge_items
    source = "alerts" if alert_items else "live_edges"

    sandbox_rows = (
        db.query(SandboxPredictionV4)
        .filter(SandboxPredictionV4.game_date == today)
        .order_by(SandboxPredictionV4.v4_confidence.desc(), SandboxPredictionV4.created_at.desc())
        .all()
    )

    summaries = []
    if items:
        top = items[0]
        summaries.append(
            {
                "kind": "storyline",
                "label": "Top Storyline",
                "text": (
                    f"{top['matchup']} is the current lead angle. "
                    f"The board leans {top.get('play') or 'watch'} with "
                    f"{(top.get('edge_pct') or 0) * 100:.1f}% edge support and "
                    f"{top.get('confidence') or 'unknown'} confidence."
                ),
            }
        )

    watch = next(
        (
            row
            for row in sandbox_rows
            if row.f5_pick and row.bullpen_convergence and row.v4_confidence is not None
        ),
        None,
    )
    if watch:
        summaries.append(
            {
                "kind": "watchlist",
                "label": "Sandbox Watch",
                "text": (
                    f"{watch.away_team} @ {watch.home_team} is on the sandbox watchlist. "
                    f"v0.4 likes {watch.f5_pick} on the F5 with "
                    f"{watch.v4_confidence * 100:.1f}% confidence"
                    + (
                        " and bullpen convergence in play."
                        if watch.bullpen_convergence
                        else "."
                    )
                ),
            }
        )

    disagreement = next(
        (
            row
            for row in sandbox_rows
            if (
                row.v3_v4_agreement is False
                or (
                    row.full_game_projected_total is not None
                    and row.v3_projected_total is not None
                    and abs(row.full_game_projected_total - row.v3_projected_total) >= 0.7
                )
            )
        ),
        None,
    )
    if disagreement:
        total_delta = None
        if disagreement.full_game_projected_total is not None and disagreement.v3_projected_total is not None:
            total_delta = disagreement.full_game_projected_total - disagreement.v3_projected_total
        summaries.append(
            {
                "kind": "disagreement",
                "label": "Model Disagreement",
                "text": (
                    f"{disagreement.away_team} @ {disagreement.home_team} is a split-board game. "
                    + (
                        f"v0.4 is {abs(total_delta):.1f} runs "
                        f"{'higher' if total_delta and total_delta > 0 else 'lower'} than v0.3 on the total."
                        if total_delta is not None
                        else "v0.3 and v0.4 are not aligned on the best angle."
                    )
                ),
            }
        )

    return {
        "source": source,
        "summaries": summaries,
        "items": items,
    }

