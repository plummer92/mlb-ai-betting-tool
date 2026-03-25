from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from scipy.stats import norm

from app.db import get_db
from app.models.schema import Game, Prediction, GameOdds, EdgeResult
from app.services.ev_math import calc_ev
from app.services.ev_math import american_to_decimal

router = APIRouter(prefix="/api/edges", tags=["edges"])

TOTAL_STD_DEV = 1.5


@router.post("/calculate/{game_id}")
def calculate_edge(game_id: int, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.game_id == game_id).first()
    prediction = db.query(Prediction).filter(Prediction.game_id == game_id).first()
    odds = db.query(GameOdds).filter(GameOdds.game_id == game_id).first()

    if not game or not prediction or not odds:
        return {"error": "Missing game, prediction, or odds"}

    model_away_win_pct = float(prediction.away_win_pct)
    model_home_win_pct = float(prediction.home_win_pct)

    implied_away_pct = float(1 / american_to_decimal(odds.away_ml))
    implied_home_pct = float(1 / american_to_decimal(odds.home_ml))

    edge_away = float(model_away_win_pct - implied_away_pct)
    edge_home = float(model_home_win_pct - implied_home_pct)

    ev_away = float(calc_ev(model_away_win_pct, american_to_decimal(odds.away_ml)))
    ev_home = float(calc_ev(model_home_win_pct, american_to_decimal(odds.home_ml)))

    model_total = float(prediction.projected_total)
    book_total = float(odds.total_line) if odds.total_line else None

    if book_total:
        model_over_prob = float(1 - norm.cdf(book_total, loc=model_total, scale=TOTAL_STD_DEV))
        model_under_prob = float(1 - model_over_prob)

        ev_over = float(calc_ev(model_over_prob, american_to_decimal(odds.over_odds)))
        ev_under = float(calc_ev(model_under_prob, american_to_decimal(odds.under_odds)))

        total_edge = float(model_total - book_total)
    else:
        ev_over = None
        ev_under = None
        total_edge = None

    if ev_away > ev_home:
        recommended_play = "AWAY"
        edge_pct = ev_away
    else:
        recommended_play = "HOME"
        edge_pct = ev_home

    confidence_tier = "HIGH" if abs(edge_pct) > 0.05 else "MEDIUM"

    edge_result = EdgeResult(
        game_id=game_id,
        prediction_id=prediction.prediction_id,
        odds_id=odds.id,
        model_away_win_pct=model_away_win_pct,
        model_home_win_pct=model_home_win_pct,
        implied_away_pct=implied_away_pct,
        implied_home_pct=implied_home_pct,
        edge_away=edge_away,
        edge_home=edge_home,
        ev_away=ev_away,
        ev_home=ev_home,
        model_total=model_total,
        book_total=book_total,
        total_edge=total_edge,
        ev_over=ev_over,
        ev_under=ev_under,
        recommended_play=recommended_play,
        confidence_tier=confidence_tier,
        edge_pct=float(edge_pct),
    )

    db.add(edge_result)
    db.commit()

    return {"message": "Edge calculated"}


@router.get("/top")
def get_top_edges(db: Session = Depends(get_db)):
    results = db.query(EdgeResult).order_by(EdgeResult.edge_pct.desc()).limit(10).all()

    return [
        {
            "game_id": r.game_id,
            "play": r.recommended_play,
            "edge": r.edge_pct,
            "confidence": r.confidence_tier
        }
        for r in results
    ]
