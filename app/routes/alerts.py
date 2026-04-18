from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import BetAlert, EdgeResult, Game, GameOutcomeReview, Prediction

CURRENT_MODEL = "v0.2-backtest-weighted"
from app.services.alert_service import create_and_send_alerts_for_today
from app.services.review_service import get_accuracy_segmented, resolve_completed_games
from app.services.synopsis_service import build_edge_synopsis

router = APIRouter(prefix="/api", tags=["alerts"])
ET = ZoneInfo("America/New_York")


@router.post("/alerts/run")
def run_alerts(db: Session = Depends(get_db)):
    return create_and_send_alerts_for_today(db)


@router.post("/alerts/send")
def send_alerts(db: Session = Depends(get_db)):
    result = create_and_send_alerts_for_today(db)
    return {
        "sent": result.get("sent", 0),
        "skipped": result.get("skipped", 0),
        "failed": result.get("failed", 0),
    }


@router.get("/alerts/today")
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

    if alert_rows:
        return {
            "source": "alerts",
            "items": [
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
            ],
        }

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

    items = []
    for edge, game in list(latest_by_game.values())[:limit]:
        synopsis, _ = build_edge_synopsis(game, edge)
        items.append(
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

    return {
        "source": "live_edges",
        "items": items,
    }


@router.post("/reviews/resolve")
def resolve_reviews(db: Session = Depends(get_db)):
    return resolve_completed_games(db)


@router.get("/reviews/recent")
def recent_reviews(limit: int = 25, db: Session = Depends(get_db)):
    rows = db.query(GameOutcomeReview).order_by(GameOutcomeReview.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "game_id": r.game_id,
            "recommended_play": r.recommended_play,
            "bet_result": r.bet_result,
            "final_away_score": r.final_away_score,
            "final_home_score": r.final_home_score,
            "pre_game_synopsis": r.pre_game_synopsis,
            "actual_outcome_summary": r.actual_outcome_summary,
            "was_model_correct": r.was_model_correct,
            "total_correct": r.total_correct,
            "projected_away_score": float(r.projected_away_score) if r.projected_away_score is not None else None,
            "projected_home_score": float(r.projected_home_score) if r.projected_home_score is not None else None,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/reviews/accuracy")
def reviews_accuracy(db: Session = Depends(get_db)):
    # Calculate segmented accuracy using the service
    segmented = get_accuracy_segmented(db, CURRENT_MODEL)
    
    # We still want to include last_10 and current model info for completeness
    last_10_rows = (
        db.query(GameOutcomeReview)
        .order_by(GameOutcomeReview.created_at.desc())
        .limit(10)
        .all()
    )
    
    last_10 = [
        {
            "game_id": r.game_id,
            "game_date": str(r.game_date),
            "predicted_winner": (
                "away" if (r.model_away_win_pct or 0) >= (r.model_home_win_pct or 0) else "home"
            ),
            "actual_winner": r.winning_side,
            "was_correct": r.was_model_correct,
            "projected_away": float(r.projected_away_score) if r.projected_away_score is not None else None,
            "projected_home": float(r.projected_home_score) if r.projected_home_score is not None else None,
            "actual_away": r.final_away_score,
            "actual_home": r.final_home_score,
            "model_total": float(r.model_total) if r.model_total is not None else None,
            "actual_total": (r.final_away_score or 0) + (r.final_home_score or 0),
            "total_correct": r.total_correct,
            "recommended_play": r.recommended_play,
            "bet_result": r.bet_result,
        }
        for r in last_10_rows
    ]

    return {
        "overall": segmented["overall"],
        "moneyline": segmented["moneyline"],
        "totals": segmented["totals"],
        "run_line": segmented["run_line"],
        "confidence_bins": segmented["confidence_bins"],
        "current_model": CURRENT_MODEL,
        "last_10": last_10,
    }
