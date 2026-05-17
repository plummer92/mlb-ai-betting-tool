from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import Game, GameOutcomeReview
from app.services.market_audit_service import get_movement_backtest_report
from app.services.profitability_report_service import get_profitability_report
from app.services.review_service import get_accuracy_segmented, resolve_completed_games

router = APIRouter(prefix="/api/reviews", tags=["reviews"])
CURRENT_MODEL = "v0.2-backtest-weighted"


@router.post("/resolve")
def resolve_reviews(db: Session = Depends(get_db)):
    return resolve_completed_games(db)


@router.get("/recent")
def get_recent_reviews(
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    # Get latest review per game (stable + simple)
    subq = (
        db.query(
            GameOutcomeReview.game_id,
            func.max(GameOutcomeReview.id).label("max_id")
        )
        .group_by(GameOutcomeReview.game_id)
        .subquery()
    )

    rows = (
        db.query(GameOutcomeReview, Game)
        .join(subq, GameOutcomeReview.id == subq.c.max_id)
        .join(Game, Game.game_id == GameOutcomeReview.game_id)
        .order_by(GameOutcomeReview.game_date.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "date": str(r.game_date),
            "matchup": f"{g.away_team} @ {g.home_team}",
            "away_team": g.away_team,
            "home_team": g.home_team,
            "predicted_side": r.recommended_play,
            "edge_pct": float(r.edge_pct) if r.edge_pct is not None else None,
            "ev": float(r.ev) if r.ev is not None else None,
            "final_score": f"{r.final_away_score}-{r.final_home_score}",
            "actual_winner": r.winning_side,
            "bet_result": r.bet_result,
            "model_correct": r.was_model_correct,
            "actual_outcome_summary": r.actual_outcome_summary,
            "projected_away_score": float(r.projected_away_score) if r.projected_away_score is not None else None,
            "projected_home_score": float(r.projected_home_score) if r.projected_home_score is not None else None,
            "model_total": float(r.model_total) if r.model_total is not None else None,
            "actual_total": (r.final_away_score or 0) + (r.final_home_score or 0),
            "confidence_tier": r.confidence_tier,
        }
        for r, g in rows
    ]


@router.get("/summary")
def get_review_summary(db: Session = Depends(get_db)):
    total = db.query(GameOutcomeReview).count()
    if total == 0:
        return {"total_predictions": 0}

    model_correct = db.query(GameOutcomeReview).filter(
        GameOutcomeReview.was_model_correct == True
    ).count()

    wins = db.query(GameOutcomeReview).filter(
        GameOutcomeReview.bet_result == "win"
    ).count()
    losses = db.query(GameOutcomeReview).filter(
        GameOutcomeReview.bet_result == "loss"
    ).count()
    pushes = db.query(GameOutcomeReview).filter(
        GameOutcomeReview.bet_result == "push"
    ).count()
    no_bet = db.query(GameOutcomeReview).filter(
        GameOutcomeReview.bet_result == "no_bet"
    ).count()

    bets_graded = wins + losses + pushes
    win_rate = round(wins / (wins + losses), 4) if (wins + losses) > 0 else None

    roi = None
    if bets_graded > 0:
        profit = wins * (100 / 110) - losses * 1.0
        roi = round(profit / bets_graded, 4)

    return {
        "total_predictions": total,
        "model_directional_accuracy": round(model_correct / total, 4),
        "bets_graded": bets_graded,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "no_bet": no_bet,
        "win_rate": win_rate,
        "roi_flat_110": roi,
    }


@router.get("/accuracy")
def reviews_accuracy(db: Session = Depends(get_db)):
    segmented = get_accuracy_segmented(db, CURRENT_MODEL)
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


@router.get("/profitability-report")
def profitability_report(
    min_sample: int = Query(default=5, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return get_profitability_report(db, min_sample=min_sample)


@router.get("/movement-report")
def movement_report(
    min_sample: int = Query(default=3, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return get_movement_backtest_report(db, min_sample=min_sample)
