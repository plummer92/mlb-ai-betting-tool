from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.schema import BetAlert, EdgeResult, Game, GameOutcomeReview, Prediction
from app.services.mlb_api import fetch_schedule_for_date

ET = ZoneInfo("America/New_York")


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
        return "win" if total < book_total else ("loss" if total > book_total else "push")
    if play == "over":
        if book_total is None:
            return "no_bet"
        return "win" if total > book_total else ("loss" if total < book_total else "push")
    return "no_bet"


def resolve_completed_games(db: Session) -> dict:
    """
    Grade every prediction from yesterday and today against final scores.
    Creates a GameOutcomeReview for each prediction once final scores exist,
    regardless of whether an edge or alert was generated for that game.
    Also stamps BetAlert.bet_result for any matching alert rows.
    """
    yesterday = datetime.now(ET).date() - timedelta(days=1)

    # Pull all predictions for games from yesterday / today
    def _query_rows():
        return (
            db.query(Prediction, Game)
            .join(Game, Game.game_id == Prediction.game_id)
            .filter(Game.game_date >= yesterday)
            .all()
        )

    rows = _query_rows()
    if rows:
        dates = {game.game_date for _, game in rows}
        _refresh_game_results_for_dates(db, dates)
        rows = _query_rows()

    resolved = 0
    skipped = 0

    for prediction, game in rows:
        away_score = game.final_away_score
        home_score = game.final_home_score

        if away_score is None or home_score is None:
            skipped += 1
            continue

        # Skip if already reviewed for this prediction
        existing = (
            db.query(GameOutcomeReview)
            .filter(
                GameOutcomeReview.game_id == game.game_id,
                GameOutcomeReview.prediction_id == prediction.prediction_id,
            )
            .first()
        )
        if existing:
            skipped += 1
            continue

        # ── Winner accuracy ────────────────────────────────────────────
        model_away = float(prediction.away_win_pct)
        model_home = float(prediction.home_win_pct)
        predicted_winner = "away" if model_away >= model_home else "home"
        actual_winner = "away" if away_score > home_score else "home"
        was_correct = predicted_winner == actual_winner

        # ── Totals accuracy ────────────────────────────────────────────
        actual_total = away_score + home_score
        predicted_total = float(prediction.projected_total)
        total_correct = abs(actual_total - predicted_total) <= 1.5

        # ── Edge / alert (optional) ────────────────────────────────────
        edge = (
            db.query(EdgeResult)
            .filter(
                EdgeResult.game_id == game.game_id,
                EdgeResult.prediction_id == prediction.prediction_id,
            )
            .first()
        )

        alert = None
        if edge:
            alert = (
                db.query(BetAlert)
                .filter(
                    BetAlert.game_id == game.game_id,
                    BetAlert.edge_result_id == edge.id,
                )
                .first()
            )

        book_total = float(edge.book_total) if edge and edge.book_total is not None else None
        bet_result_val = _bet_result(
            edge.recommended_play if edge else None,
            away_score,
            home_score,
            book_total,
        )

        actual_summary = (
            f"Final: {game.away_team} {away_score}, {game.home_team} {home_score}. "
            f"Winner: {actual_winner}. Total runs: {actual_total}. "
            f"Model called: {predicted_winner} (correct={was_correct}). "
            f"Projected total: {predicted_total:.1f} (within 1.5={total_correct})."
        )

        review = GameOutcomeReview(
            game_id=game.game_id,
            prediction_id=prediction.prediction_id,
            edge_result_id=edge.id if edge else None,
            bet_alert_id=alert.id if alert else None,
            game_date=game.game_date,
            pre_game_synopsis=alert.synopsis if alert else None,
            actual_outcome_summary=actual_summary,
            recommended_play=edge.recommended_play if edge else None,
            confidence_tier=edge.confidence_tier if edge else None,
            model_away_win_pct=model_away,
            model_home_win_pct=model_home,
            projected_away_score=float(prediction.projected_away_score),
            projected_home_score=float(prediction.projected_home_score),
            model_total=predicted_total,
            book_total=book_total,
            edge_pct=float(edge.edge_pct) if edge and edge.edge_pct is not None else None,
            ev=float(alert.ev) if alert else None,
            movement_direction=edge.movement_direction if edge else None,
            final_away_score=away_score,
            final_home_score=home_score,
            winning_side=actual_winner,
            bet_result=bet_result_val,
            was_model_correct=was_correct,
            total_correct=total_correct,
            top_factors_predicted=alert.rationale_json if alert else None,
            top_factors_actual=None,
        )
        db.add(review)

        # Stamp the BetAlert too if it exists and isn't already resolved
        if alert and alert.bet_result is None:
            alert.final_away_score = away_score
            alert.final_home_score = home_score
            alert.bet_result = bet_result_val
            alert.resolved_at = datetime.now(ET)

        resolved += 1

    db.commit()
    return {"resolved": resolved, "skipped": skipped}
