from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import POSTGAME_LOOKBACK_HOURS
from app.models.schema import BetAlert, EdgeResult, Game, GameOdds, GameOutcomeReview, Prediction
from app.services.mlb_api import fetch_schedule_for_date
from app.services.ev_math import american_to_decimal

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


FINAL_STATUSES = {"Final", "Game Over", "Completed Early"}


def _purge_stale_reviews(db: Session) -> int:
    """
    Delete any GameOutcomeReview rows created before a game was truly final,
    or whose stored final score no longer matches the current game row.
    Also resets the corresponding BetAlert stamps so they can be re-graded.
    """
    stale_pairs = (
        db.query(GameOutcomeReview, Game)
        .join(Game, Game.game_id == GameOutcomeReview.game_id)
        .filter(
            or_(
                Game.status.is_(None),
                ~Game.status.in_(FINAL_STATUSES),
                Game.final_away_score.is_(None),
                Game.final_home_score.is_(None),
                GameOutcomeReview.final_away_score != Game.final_away_score,
                GameOutcomeReview.final_home_score != Game.final_home_score,
            )
        )
        .all()
    )
    bad_reviews = [review for review, _ in stale_pairs]
    if not bad_reviews:
        return 0

    # Reset any BetAlert rows that were wrongly stamped from these reviews
    bad_alert_ids = {r.bet_alert_id for r in bad_reviews if r.bet_alert_id is not None}
    if bad_alert_ids:
        (
            db.query(BetAlert)
            .filter(BetAlert.id.in_(bad_alert_ids))
            .update(
                {
                    BetAlert.bet_result: None,
                    BetAlert.final_away_score: None,
                    BetAlert.final_home_score: None,
                    BetAlert.resolved_at: None,
                },
                synchronize_session=False,
            )
        )

    count = len(bad_reviews)
    for r in bad_reviews:
        db.delete(r)
    db.commit()
    return count


def resolve_completed_games(db: Session) -> dict:
    """
    Grade every prediction from yesterday and today against final scores.
    Creates a GameOutcomeReview for each prediction once final scores exist,
    regardless of whether an edge or alert was generated for that game.
    Also stamps BetAlert.bet_result for any matching alert rows.

    Skips games where the total runs scored == 0 and the status is not
    clearly final — a 0-0 score almost always means the API hasn't
    returned the real score yet (true 0-0 MLB finals are essentially
    non-existent).
    """
    # Remove any previously-created reviews that were stamped before a game
    # was truly final, or whose stored scores are now stale.
    purged = _purge_stale_reviews(db)

    cutoff_date = (datetime.now(ET) - timedelta(hours=POSTGAME_LOOKBACK_HOURS)).date()

    # Pull all predictions for games from yesterday / today
    def _query_rows():
        return (
            db.query(Prediction, Game)
            .join(Game, Game.game_id == Prediction.game_id)
            .filter(Game.game_date >= cutoff_date)
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
        game_is_final = (game.status or "").strip() in FINAL_STATUSES
        if not game_is_final:
            skipped += 1
            continue

        away_score = game.final_away_score
        home_score = game.final_home_score

        if away_score is None or home_score is None:
            skipped += 1
            continue

        # Guard against grading placeholder zero-score rows that slipped through.
        if (away_score + home_score) == 0:
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
    return {"resolved": resolved, "skipped": skipped, "purged_scoreless": purged}


from scipy.stats import norm

TOTAL_STD_DEV = 2.5


def get_accuracy_segmented(db: Session, model_version: str | None = None) -> dict:
    """
    Calculate overall and segmented accuracy/betting performance.
    """
    query = db.query(GameOutcomeReview, Prediction, GameOdds).join(
        Prediction, Prediction.prediction_id == GameOutcomeReview.prediction_id
    ).outerjoin(
        EdgeResult, EdgeResult.id == GameOutcomeReview.edge_result_id
    ).outerjoin(
        GameOdds, GameOdds.id == EdgeResult.odds_id
    )
    if model_version:
        query = query.filter(Prediction.model_version == model_version)

    pairs = query.all()

    def calc_stats(triplets: list[tuple[GameOutcomeReview, Prediction, GameOdds]]):
        # We only count rows that were actually graded as a win/loss/push
        bets = [t for t in triplets if t[0].bet_result in ("win", "loss", "push")]
        wins = sum(1 for t in bets if t[0].bet_result == "win")
        losses = sum(1 for t in bets if t[0].bet_result == "loss")
        pushes = sum(1 for t in bets if t[0].bet_result == "push")
        
        total_decisions = wins + losses
        win_rate = round(wins / total_decisions, 3) if total_decisions > 0 else 0.0
        
        # Avg Odds and Edge
        total_odds = 0.0
        total_edge = 0.0
        counted_odds = 0
        for r, p, o in bets:
            play = (r.recommended_play or "").upper()
            odds_val = None
            if o:
                if play == "AWAY_ML": odds_val = o.away_ml
                elif play == "HOME_ML": odds_val = o.home_ml
                elif play == "OVER": odds_val = o.over_odds
                elif play == "UNDER": odds_val = o.under_odds
            
            if odds_val is not None:
                total_odds += american_to_decimal(odds_val)
                total_edge += float(r.edge_pct or 0)
                counted_odds += 1
        
        avg_odds = round(total_odds / counted_odds, 3) if counted_odds > 0 else 0.0
        avg_edge = round(total_edge / counted_odds, 4) if counted_odds > 0 else 0.0

        return {
            "win_rate": win_rate,
            "bets": len(bets),
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "avg_odds": avg_odds,
            "avg_edge": avg_edge,
        }

    def get_bin_name(prob: float) -> str | None:
        if prob >= 0.80: return "80%+"
        if prob >= 0.70: return "70-79%"
        if prob >= 0.60: return "60-69%"
        if prob >= 0.50: return "50-59%"
        return None

    # Categories
    ml_reviews = []
    total_reviews = []
    rl_reviews = []

    # Market-specific bins
    ml_bins = {"50-59%": [], "60-69%": [], "70-79%": [], "80%+": []}
    total_bins = {"50-59%": [], "60-69%": [], "70-79%": [], "80%+": []}
    rl_bins = {"50-59%": [], "60-69%": [], "70-79%": [], "80%+": []}
    overall_bins = {"50-59%": [], "60-69%": [], "70-79%": [], "80%+": []}

    for r, p, o in pairs:
        play = (r.recommended_play or "").upper()
        if not play:
            continue

        # Confidence Calculation
        model_prob = 0.0
        if play == "AWAY_ML":
            model_prob = float(r.model_away_win_pct or 0)
        elif play == "HOME_ML":
            model_prob = float(r.model_home_win_pct or 0)
        elif play == "OVER":
            if r.model_total is not None and r.book_total is not None:
                model_prob = float(1 - norm.cdf(float(r.book_total), loc=float(r.model_total), scale=TOTAL_STD_DEV))
        elif play == "UNDER":
            if r.model_total is not None and r.book_total is not None:
                model_prob = float(norm.cdf(float(r.book_total), loc=float(r.model_total), scale=TOTAL_STD_DEV))
        
        bin_name = get_bin_name(model_prob)
        triplet = (r, p, o)
        if bin_name:
            overall_bins[bin_name].append(triplet)

        # Market Segmentation
        if "ML" in play:
            ml_reviews.append(triplet)
            if bin_name: ml_bins[bin_name].append(triplet)
        elif any(x in play for x in ("OVER", "UNDER")):
            total_reviews.append(triplet)
            if bin_name: total_bins[bin_name].append(triplet)
        elif any(x in play for x in ("RL", "+1.5", "-1.5")):
            rl_reviews.append(triplet)
            if bin_name: rl_bins[bin_name].append(triplet)

    all_reviews = pairs

    return {
        "overall": {
            **calc_stats(all_reviews),
            "confidence_bins": {k: calc_stats(v) for k, v in overall_bins.items()}
        },
        "moneyline": {
            **calc_stats(ml_reviews),
            "confidence_bins": {k: calc_stats(v) for k, v in ml_bins.items()}
        },
        "totals": {
            **calc_stats(total_reviews),
            "confidence_bins": {k: calc_stats(v) for k, v in total_bins.items()}
        },
        "run_line": {
            **calc_stats(rl_reviews),
            "confidence_bins": {k: calc_stats(v) for k, v in rl_bins.items()}
        },
        "confidence_bins": {k: calc_stats(v) for k, v in overall_bins.items()}
    }
