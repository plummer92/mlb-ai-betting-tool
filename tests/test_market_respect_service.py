from app.services.market_respect_service import classify_tradable_signal


def _agreed_market(line_clv: float = 0.5) -> dict:
    return {
        "score": 72,
        "tags": ["MARKET AGREED"],
        "components": {
            "line_clv": line_clv,
            "price_clv": 0.0,
            "freshness_status": "fresh",
        },
    }


def _adjustment(edge: float, ev: float, confidence: str = "strong") -> dict:
    return {
        "score": 72,
        "tags": ["MARKET AGREED"],
        "score_bucket": "market_agreement",
        "bucket": "market_agreement",
        "raw_edge_pct": edge,
        "adjusted_edge_pct": edge,
        "raw_ev": ev,
        "adjusted_ev": ev,
        "adjusted_confidence": confidence,
    }


def test_trade_requires_profitable_policy_profile():
    signal = classify_tradable_signal(
        market_respect=_agreed_market(),
        market_adjustment=_adjustment(edge=0.08, ev=0.12),
        play="home_ml",
        raw_edge_pct=0.08,
        raw_ev=0.12,
        adjusted_confidence="strong",
    )

    assert signal["tradable_signal"] == "TRADE"
    assert signal["trade_allowed"] is True
    assert signal["policy_qualified"] is True


def test_positive_clv_trade_is_blocked_outside_profitable_policy():
    signal = classify_tradable_signal(
        market_respect=_agreed_market(),
        market_adjustment=_adjustment(edge=0.14, ev=0.18),
        play="away_ml",
        raw_edge_pct=0.14,
        raw_ev=0.18,
        adjusted_confidence="strong",
    )

    assert signal["tradable_signal"] == "PASS"
    assert signal["trade_allowed"] is False
    assert signal["tradable_reason"] == "outside tightened profitable policy"
    assert signal["policy_qualified"] is False


def test_neutral_strong_model_stays_research_watch():
    signal = classify_tradable_signal(
        market_respect={
            "score": 50,
            "tags": ["MARKET NEUTRAL"],
            "components": {"freshness_status": "fresh"},
        },
        market_adjustment={
            "score": 50,
            "tags": ["MARKET NEUTRAL"],
            "score_bucket": "mixed_market",
            "bucket": "mixed_market",
            "raw_edge_pct": 0.16,
            "adjusted_edge_pct": 0.16,
            "raw_ev": 0.24,
            "adjusted_ev": 0.24,
            "adjusted_confidence": "strong",
        },
        play="under",
        raw_edge_pct=0.16,
        raw_ev=0.24,
        adjusted_confidence="strong",
    )

    assert signal["tradable_signal"] == "WATCH"
    assert signal["trade_allowed"] is False
