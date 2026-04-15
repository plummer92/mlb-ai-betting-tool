def american_to_decimal(american: int) -> float:
    """Convert American odds to decimal (European) format."""
    if american > 0:
        return (american / 100) + 1
    else:
        return (100 / abs(american)) + 1


def implied_prob_raw(american: int) -> float:
    """Raw implied probability from American odds (includes vig)."""
    if american > 0:
        return 100 / (american + 100)
    else:
        return abs(american) / (abs(american) + 100)


# Alias used in movement context
ml_to_implied_prob = implied_prob_raw


def remove_vig(prob_a: float, prob_b: float) -> tuple[float, float]:
    """
    Remove the bookmaker's vig (overround) from a two-sided market.
    Returns fair probabilities that sum to 1.0.

    Example: -130 / +110 → raw 0.565 + 0.476 = 1.041 (4.1% vig)
    After removal: 0.543 / 0.457
    """
    total = prob_a + prob_b
    return prob_a / total, prob_b / total


def calc_ev(model_prob: float, decimal_odds: float) -> float:
    """
    Expected value as a percentage of stake.

    EV% = (model_prob × (decimal_odds - 1)) - (1 - model_prob)

    Positive EV means the bet is +expected value at this price.
    e.g. EV = +0.042 means +4.2% edge per unit wagered.
    """
    win_return = model_prob * (decimal_odds - 1)
    loss_cost = (1 - model_prob) * 1
    return win_return - loss_cost


def calc_edge(model_prob: float, implied_prob: float) -> float:
    """
    Raw edge = model probability minus vig-removed implied probability.
    Positive = model likes this side more than the market does.
    """
    return model_prob - implied_prob


def kelly_fraction(
    model_prob: float,
    decimal_odds: float,
    kelly_multiplier: float = 0.25,
) -> float:
    """
    Quarter-Kelly stake sizing (conservative, standard for sports betting).
    Returns fraction of bankroll to wager. Returns 0 if negative EV.

    Full Kelly = (bp - q) / b  where b = decimal_odds - 1, p = model_prob, q = 1 - p
    """
    b = decimal_odds - 1
    p = model_prob
    q = 1 - p
    full_kelly = (b * p - q) / b
    if full_kelly <= 0:
        return 0.0
    return full_kelly * kelly_multiplier


def confidence_tier(edge: float, ev: float) -> str | None:
    """
    Classify edge strength. Tune these thresholds once you have
    enough historical data to validate against actual outcomes.
    """
    if edge >= 0.07 and ev >= 0.05:
        return "strong"
    elif edge >= 0.04 and ev >= 0.025:
        return "medium"
    elif edge >= 0.02 and ev >= 0.01:
        return "weak"
    return None  # no play


def recommended_play(
    edge_away: float,
    ev_away: float,
    edge_home: float,
    ev_home: float,
    edge_over: float,
    ev_over: float,
    edge_under: float,
    ev_under: float,
    min_edge: float = 0.02,
    model_away: float | None = None,
    model_home: float | None = None,
) -> str | None:
    """
    Pick the single best play across all four markets.
    Returns None if nothing clears minimum edge threshold.

    When model_away/model_home are provided, the moneyline recommendation
    is restricted to whichever side the model gives higher win probability.
    This prevents recommending the opposite side of the model's predicted
    winner (e.g. model calls home but alert fires away_ml).
    """
    candidates = [
        ("away_ml", edge_away, ev_away),
        ("home_ml", edge_home, ev_home),
        ("over", edge_over, ev_over),
        ("under", edge_under, ev_under),
    ]

    # Only recommend the moneyline side the model believes is more likely to win
    if model_away is not None and model_home is not None:
        model_preferred_ml = "away_ml" if model_away >= model_home else "home_ml"
        candidates = [
            (name, edge, ev)
            for name, edge, ev in candidates
            if name not in ("away_ml", "home_ml") or name == model_preferred_ml
        ]

    plays = [
        (name, edge, ev)
        for name, edge, ev in candidates
        if edge >= min_edge and ev > 0
    ]
    if not plays:
        return None
    # Best play = highest EV among qualifying edges
    return max(plays, key=lambda x: x[2])[0]


def prob_move(open_ml: int, close_ml: int) -> float:
    """
    How much did the implied probability shift between open and close?
    Returns signed value in probability points.

    Example:
      open  -130 → implied 0.565
      close -150 → implied 0.600
      move  = +0.035 (market moved 3.5 pts toward this side)
    """
    return ml_to_implied_prob(close_ml) - ml_to_implied_prob(open_ml)


def is_sharp_move(
    open_away_ml: int,
    close_away_ml: int,
    open_home_ml: int,
    close_home_ml: int,
) -> tuple[bool, bool]:
    """
    Reverse line move (RLM) detection — the classic sharp signal.

    Uses move magnitude as a proxy: a move > 4 implied probability points
    is treated as potentially sharp. For a proper RLM detector you'd also
    need betting % data (Action Network, etc.) — flag these for review.
    """
    away_move = prob_move(open_away_ml, close_away_ml)
    home_move = prob_move(open_home_ml, close_home_ml)

    SHARP_THRESHOLD = 0.04  # 4 implied probability points

    sharp_away = away_move > SHARP_THRESHOLD
    sharp_home = home_move > SHARP_THRESHOLD

    return sharp_away, sharp_home


def movement_ev_boost(
    model_prob: float,
    sharp_toward_model_side: bool,
    against_model_side: bool,
) -> float:
    """
    Adjust EV based on line movement signal.

    If sharp money is moving toward the same side your model likes,
    that's corroborating signal — small upward EV adjustment.

    If sharp money is moving against your model, that's a warning —
    small downward adjustment (not a kill switch, just a discount).

    These multipliers are intentionally conservative. Tune with data.
    """
    if sharp_toward_model_side:
        return 0.015   # +1.5% EV boost for corroborating sharp move
    if against_model_side:
        return -0.020  # -2.0% EV penalty for divergent sharp move
    return 0.0
