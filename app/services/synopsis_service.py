from __future__ import annotations

from decimal import Decimal


def _to_float(value) -> float:
    if value is None:
        return 0.0
    return float(value if not isinstance(value, Decimal) else value)


def build_edge_synopsis(game, edge) -> tuple[str, dict]:
    away = getattr(game, "away_team", "Away")
    home = getattr(game, "home_team", "Home")
    play = getattr(edge, "recommended_play", None)
    confidence = getattr(edge, "confidence_tier", None) or "unknown"

    edge_pct = _to_float(getattr(edge, "edge_pct", 0))
    total_edge = _to_float(getattr(edge, "total_edge", 0))
    model_total = _to_float(getattr(edge, "model_total", 0))
    book_total = _to_float(getattr(edge, "book_total", 0))
    away_edge = _to_float(getattr(edge, "edge_away", 0))
    home_edge = _to_float(getattr(edge, "edge_home", 0))
    ev_away = _to_float(getattr(edge, "ev_away", 0))
    ev_home = _to_float(getattr(edge, "ev_home", 0))
    ev_under = _to_float(getattr(edge, "ev_under", 0))
    ev_over = _to_float(getattr(edge, "ev_over", 0))
    movement_direction = getattr(edge, "movement_direction", None)

    factors = []

    if play == "away_ml":
        factors.append(f"model win probability favors {away} by {away_edge:.1%} versus the market")
        factors.append(f"estimated value on {away} moneyline is {ev_away:.1%}")
    elif play == "home_ml":
        factors.append(f"model win probability favors {home} by {home_edge:.1%} versus the market")
        factors.append(f"estimated value on {home} moneyline is {ev_home:.1%}")
    elif play == "under":
        factors.append(f"model total {model_total:.1f} sits below the book total of {book_total:.1f}")
        factors.append(f"estimated value on the under is {ev_under:.1%}")
    elif play == "over":
        factors.append(f"model total {model_total:.1f} sits above the book total of {book_total:.1f}")
        factors.append(f"estimated value on the over is {ev_over:.1%}")
    else:
        factors.append("the model did not find a bet large enough to clear the alert threshold")

    if abs(total_edge) >= 1.0:
        if total_edge < 0:
            factors.append("the matchup projects as lower scoring than the market expects")
        else:
            factors.append("the matchup projects as higher scoring than the market expects")

    if movement_direction == "toward_model":
        factors.append("late market movement has moved toward the model")
    elif movement_direction == "away_from_model":
        factors.append("late market movement has moved against the model")

    factors.append(f"confidence is graded {confidence}")

    synopsis = (
        f"{away} at {home}: "
        + (f"best play is {play}. " if play else "no qualifying play. ")
        + "Why: " + "; ".join(factors) + "."
    )

    return synopsis, {
        "play": play,
        "edge_pct": edge_pct,
        "total_edge": total_edge,
        "model_total": model_total,
        "book_total": book_total,
        "movement_direction": movement_direction,
        "factors": factors,
    }


def build_postgame_summary(game, edge, final_away_score: int, final_home_score: int, bet_result: str) -> tuple[str, str]:
    away = getattr(game, "away_team", "Away")
    home = getattr(game, "home_team", "Home")
    recommended_play = getattr(edge, "recommended_play", None)
    winning_side = away if final_away_score > final_home_score else home
    total_runs = final_away_score + final_home_score
    model_total = _to_float(getattr(edge, "model_total", 0))
    book_total = _to_float(getattr(edge, "book_total", 0))

    actual = (
        f"Final score: {away} {final_away_score}, {home} {final_home_score}. "
        f"Winner: {winning_side}. Total runs: {total_runs}. "
        f"Model recommendation: {recommended_play or 'none'}. Bet result: {bet_result}."
    )

    top_actual = []
    if book_total:
        if total_runs < book_total:
            top_actual.append("the game finished below the market total")
        elif total_runs > book_total:
            top_actual.append("the game finished above the market total")
        else:
            top_actual.append("the game landed on the market total")

    if model_total:
        if abs(total_runs - model_total) <= 1.0:
            top_actual.append("the actual scoring finished close to the model projection")
        else:
            top_actual.append("the actual scoring diverged meaningfully from the model projection")

    return actual, "; ".join(top_actual) if top_actual else "no major postgame factor summary available"
