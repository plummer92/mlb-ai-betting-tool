from __future__ import annotations


BETTING_PROFILES: dict[str, dict] = {
    # Profitability audit: home moneylines were the only clearly positive
    # market across multiple edge buckets, so they stay broadly enabled.
    "home_ml": {
        "enabled": True,
        "min_edge": 0.05,
        "max_edge": None,
        "min_ev": 0.03,
        "allowed_confidences": {"medium", "strong"},
    },
    # Away ML performed well only in the moderate 5-10% edge band.
    "away_ml": {
        "enabled": True,
        "min_edge": 0.05,
        "max_edge": 0.10,
        "min_ev": 0.05,
        "allowed_confidences": {"medium", "strong"},
    },
    # Overs also worked best in the moderate edge band; the highest-edge tails
    # were historically some of the worst-performing spots.
    "over": {
        "enabled": True,
        "min_edge": 0.05,
        "max_edge": 0.10,
        "min_ev": 0.08,
        "allowed_confidences": {"medium", "strong"},
    },
    # Unders were the weakest overall market in the current history, so they
    # are disabled until the model is recalibrated.
    "under": {
        "enabled": False,
        "min_edge": 0.05,
        "max_edge": 0.10,
        "min_ev": 0.08,
        "allowed_confidences": {"medium", "strong"},
    },
}


def get_betting_profile(play: str | None) -> dict:
    return BETTING_PROFILES.get((play or "").lower(), {"enabled": False})


def qualifies_for_bet_policy(
    *,
    play: str | None,
    edge_pct: float | None,
    ev: float | None,
    confidence: str | None,
) -> bool:
    profile = get_betting_profile(play)
    if not profile.get("enabled"):
        return False

    edge = float(edge_pct or 0.0)
    expected_value = float(ev or 0.0)
    normalized_confidence = (confidence or "").strip().lower()

    if expected_value < float(profile["min_ev"]):
        return False
    if edge < float(profile["min_edge"]):
        return False

    max_edge = profile.get("max_edge")
    if max_edge is not None and edge >= float(max_edge):
        return False

    allowed_confidences = profile.get("allowed_confidences") or set()
    if allowed_confidences and normalized_confidence not in allowed_confidences:
        return False

    return True
