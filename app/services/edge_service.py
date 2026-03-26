from datetime import datetime, timezone

from scipy.stats import norm
from sqlalchemy import desc
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.schema import EdgeResult, Game, GameOdds, LineMovement, Prediction
from app.services.ev_math import (
    american_to_decimal,
    calc_edge,
    calc_ev,
    confidence_tier,
    implied_prob_raw,
    movement_ev_boost,
    recommended_play,
    remove_vig,
)

# Typical MLB run total standard deviation — tune once you have data
TOTAL_STD_DEV = 2.5


def calculate_edge_for_game(
    db: Session,
    game_id: int,
    movement: LineMovement | None = None,
) -> EdgeResult | None:
    """
    Pull latest prediction + latest odds snapshot for a game,
    run all EV math, persist and return the EdgeResult.
    """
    prediction = (
        db.query(Prediction)
        .filter(Prediction.game_id == game_id)
        .order_by(desc(Prediction.created_at))
        .first()
    )
    odds = (
        db.query(GameOdds)
        .filter(GameOdds.game_id == game_id)
        .order_by(desc(GameOdds.fetched_at))
        .first()
    )

    if not prediction or not odds:
        return None

    # ── Moneyline math ──────────────────────────────────
    raw_away = implied_prob_raw(odds.away_ml)
    raw_home = implied_prob_raw(odds.home_ml)
    imp_away, imp_home = remove_vig(raw_away, raw_home)

    model_away = float(prediction.away_win_pct)
    model_home = float(prediction.home_win_pct)

    edge_away = calc_edge(model_away, imp_away)
    edge_home = calc_edge(model_home, imp_home)

    ev_away = calc_ev(model_away, american_to_decimal(odds.away_ml))
    ev_home = calc_ev(model_home, american_to_decimal(odds.home_ml))

    # ── Totals math ─────────────────────────────────────
    ev_over = ev_under = total_edge = 0.0
    edge_over = edge_under = 0.0

    if odds.total_line and odds.over_odds and odds.under_odds:
        model_total = float(prediction.projected_total)
        book_total = float(odds.total_line)

        # Normal distribution approximation for over/under probability.
        model_over_prob = float(1 - norm.cdf(book_total, loc=model_total, scale=TOTAL_STD_DEV))
        model_under_prob = 1 - model_over_prob

        raw_over = implied_prob_raw(odds.over_odds)
        raw_under = implied_prob_raw(odds.under_odds)
        imp_over, imp_under = remove_vig(raw_over, raw_under)

        edge_over  = calc_edge(model_over_prob,  imp_over)
        edge_under = calc_edge(model_under_prob, imp_under)

        ev_over  = calc_ev(model_over_prob,  american_to_decimal(odds.over_odds))
        ev_under = calc_ev(model_under_prob, american_to_decimal(odds.under_odds))
        total_edge = model_total - book_total

    # ── Movement signal ──────────────────────────────────
    if movement is None:
        movement = db.query(LineMovement).filter(LineMovement.game_id == game_id).first()

    away_boost = home_boost = 0.0
    movement_id = None

    if movement:
        movement_id = movement.id
        sharp_agrees_away = movement.sharp_away and (model_away > imp_away)
        sharp_against_away = movement.sharp_home and (model_away > imp_away)

        sharp_agrees_home = movement.sharp_home and (model_home > imp_home)
        sharp_against_home = movement.sharp_away and (model_home > imp_home)

        away_boost = movement_ev_boost(model_away, sharp_agrees_away, sharp_against_away)
        home_boost = movement_ev_boost(model_home, sharp_agrees_home, sharp_against_home)

    ev_away_final = ev_away + away_boost
    ev_home_final = ev_home + home_boost
    net_boost = away_boost + home_boost

    # ── Best play + tier ────────────────────────────────
    play = recommended_play(
        edge_away, ev_away_final,
        edge_home, ev_home_final,
        edge_over, ev_over,
        edge_under, ev_under,
    )
    max_edge = max(abs(edge_away), abs(edge_home), abs(edge_over), abs(edge_under))
    max_ev = max(ev_away_final, ev_home_final, ev_over, ev_under)
    tier = confidence_tier(max_edge, max_ev)

    # ── Movement direction relative to model recommendation ──
    movement_direction: str | None = None
    if movement:
        model_prefers_away = model_away > model_home
        if model_prefers_away:
            if movement.sharp_away:
                movement_direction = "toward_model"
            elif movement.sharp_home:
                movement_direction = "away_from_model"
            else:
                movement_direction = "neutral"
        else:
            if movement.sharp_home:
                movement_direction = "toward_model"
            elif movement.sharp_away:
                movement_direction = "away_from_model"
            else:
                movement_direction = "neutral"

    row = dict(
        game_id=game_id,
        prediction_id=prediction.prediction_id,
        odds_id=odds.id,
        movement_id=movement_id,
        calculated_at=datetime.now(timezone.utc),
        model_away_win_pct=round(model_away, 4),
        model_home_win_pct=round(model_home, 4),
        implied_away_pct=round(imp_away, 4),
        implied_home_pct=round(imp_home, 4),
        edge_away=round(edge_away, 4),
        edge_home=round(edge_home, 4),
        ev_away=round(ev_away_final, 4),
        ev_home=round(ev_home_final, 4),
        movement_boost=round(net_boost, 4),
        model_total=prediction.projected_total,
        book_total=odds.total_line,
        total_edge=round(total_edge, 4),
        ev_over=round(ev_over, 4),
        ev_under=round(ev_under, 4),
        recommended_play=play,
        confidence_tier=tier,
        edge_pct=round(max_edge, 4),
        movement_direction=movement_direction,
    )

    update_cols = {k: v for k, v in row.items() if k not in ("game_id", "prediction_id")}
    stmt = (
        pg_insert(EdgeResult)
        .values(**row)
        .on_conflict_do_update(
            constraint="uq_edge_game_prediction",
            set_=update_cols,
        )
    )
    result = db.execute(stmt)
    db.commit()

    # Return the refreshed row
    return (
        db.query(EdgeResult)
        .filter(
            EdgeResult.game_id == game_id,
            EdgeResult.prediction_id == prediction.prediction_id,
        )
        .first()
    )


def calculate_all_edges_today(db: Session) -> list[EdgeResult]:
    today = datetime.now(timezone.utc).date()
    games = db.query(Game).filter(Game.game_date == today).all()
    return [r for g in games if (r := calculate_edge_for_game(db, g.game_id))]
