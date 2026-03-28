from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.schema import Game, GameOutcomeReview
from app.services.review_service import resolve_completed_games

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


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
