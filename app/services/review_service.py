from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import POSTGAME_LOOKBACK_HOURS
from app.models.schema import BetAlert, EdgeResult, Game, GameOutcomeReview, Prediction
from app.services.mlb_api import fetch_schedule_for_date
from app.services.synopsis_service import build_postgame_summary

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _parse_start_time(start_time: str | None):
    if not start_time:
        return None
    try:
        dt = datetime.fromisoformat(start_time)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return None


def _refresh_game_results_for_dates(db: Session, dates: set):
    for game_date in dates:
        refreshed = fetch_schedule_for_date(str(game_date))
        for g in refreshed:
            existing = db.query(Game).filter(Game.game_id == g["game_id"]).first()
            if existing:
                existing.status = g["status"]
                existing.final_away_score = g["final_away_score"]
                existing.final_home_score = g["final_home_score"]
        db.commit()


def _bet_result(play: str | None, away_score: int, home_score: int, book_total: float | None) -> str:
    if not play:
        return "no_bet"

    total = away_score + home_score

    if play == "away_ml":
        return "win" if away_score > home_score else "loss"
    if play == "home_ml":
        return "win" if home_score > away_score else "loss"
    if play == "under":
        if book_total is None:
            return "no_bet"
        if total < book_total:
            return "win"
        if total > book_total:
            return "loss"
        return "push"
    if play == "over":
        if book_total is None:
            return "no_bet"
        if total > book_total:
            return "win"
        if total < book_total:
            return "loss"
        return "push"
    return "no_bet"


def resolve_completed_games(db: Session) -> dict:
    now_et = datetime.now(ET)
    cutoff = now_et - timedelta(hours=POSTGAME_LOOKBACK_HOURS)

    rows = (
        db.query(BetAlert, Game, EdgeResult, Prediction)
        .join(Game, Game.game_id == BetAlert.game_id)
        .join(EdgeResult, EdgeResult.id == BetAlert.edge_result_id)
        .join(Prediction, Prediction.prediction_id == BetAlert.prediction_id)
        .filter(BetAlert.bet_result.is_(None))
        .all()
    )

    if rows:
        _refresh_game_results_for_dates(db, {game.game_date for _, game, _, _ in rows})

    resolved = 0
    skipped = 0

    rows = (
        db.query(BetAlert, Game, EdgeResult, Prediction)
        .join(Game, Game.game_id == BetAlert.game_id)
        .join(EdgeResult, EdgeResult.id == BetAlert.edge_result_id)
        .join(Prediction, Prediction.prediction_id == BetAlert.prediction_id)
        .filter(BetAlert.bet_result.is_(None))
        .all()
    )

    for alert, game, edge, prediction in rows:
        game_dt = _parse_start_time(game.start_time)
        if game_dt is None:
            skipped += 1
            continue

        if game_dt.astimezone(ET) > cutoff:
            skipped += 1
            continue

        away_score = game.final_away_score
        home_score = game.final_home_score
        if away_score is None or home_score is None:
            skipped += 1
            continue

        bet_result = _bet_result(
            edge.recommended_play,
            away_score,
            home_score,
            float(edge.book_total) if edge.book_total is not None else None,
        )
        winning_side = "away" if away_score > home_score else "home"
        was_model_correct = (
            (winning_side == "away" and float(edge.model_away_win_pct or 0) >= 0.5)
            or (winning_side == "home" and float(edge.model_home_win_pct or 0) >= 0.5)
        )

        actual_summary, top_actual = build_postgame_summary(game, edge, away_score, home_score, bet_result)

        existing_review = (
            db.query(GameOutcomeReview)
            .filter(
                GameOutcomeReview.game_id == game.game_id,
                GameOutcomeReview.prediction_id == prediction.prediction_id,
                GameOutcomeReview.edge_result_id == edge.id,
            )
            .first()
        )

        if not existing_review:
            review = GameOutcomeReview(
                game_id=game.game_id,
                prediction_id=prediction.prediction_id,
                edge_result_id=edge.id,
                bet_alert_id=alert.id,
                game_date=game.game_date,
                pre_game_synopsis=alert.synopsis,
                actual_outcome_summary=actual_summary,
                recommended_play=edge.recommended_play,
                confidence_tier=edge.confidence_tier,
                model_away_win_pct=edge.model_away_win_pct,
                model_home_win_pct=edge.model_home_win_pct,
                model_total=edge.model_total,
                book_total=edge.book_total,
                edge_pct=edge.edge_pct,
                ev=alert.ev,
                movement_direction=getattr(edge, "movement_direction", None),
                final_away_score=away_score,
                final_home_score=home_score,
                winning_side=winning_side,
                bet_result=bet_result,
                was_model_correct=was_model_correct,
                top_factors_predicted=alert.rationale_json,
                top_factors_actual=top_actual,
            )
            db.add(review)

        alert.final_away_score = away_score
        alert.final_home_score = home_score
        alert.bet_result = bet_result
        alert.resolved_at = datetime.now(ET)
        resolved += 1

    db.commit()
    return {"resolved": resolved, "skipped": skipped}
