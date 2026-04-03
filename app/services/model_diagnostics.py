from __future__ import annotations


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize_probability_diagnostics(results: list[dict], *, label: str) -> dict:
    if not results:
        summary = {"label": label, "games": 0}
        print(f"[model] {summary}", flush=True)
        return summary

    favorite_probs = [max(float(r["home_win_pct"]), float(r["away_win_pct"])) for r in results]
    confidences = [float(r.get("confidence_score") or 0.0) for r in results]
    market_deltas = [abs(float(r["market_delta"])) for r in results if r.get("market_delta") is not None]
    logistic_deltas = [abs(float(r["logistic_delta"])) for r in results if r.get("logistic_delta") is not None]

    summary = {
        "label": label,
        "games": len(results),
        "avg_favorite_prob": round(_mean(favorite_probs), 4),
        "gt_65": sum(1 for v in favorite_probs if v > 0.65),
        "gt_70": sum(1 for v in favorite_probs if v > 0.70),
        "gt_80": sum(1 for v in favorite_probs if v > 0.80),
        "avg_confidence": round(_mean(confidences), 2),
        "avg_abs_market_delta": round(_mean(market_deltas), 4) if market_deltas else None,
        "avg_abs_logistic_delta": round(_mean(logistic_deltas), 4) if logistic_deltas else None,
    }
    print(f"[model] {summary}", flush=True)
    return summary


def summarize_edge_diagnostics(edge_results: list[dict], *, label: str) -> dict:
    created = [row for row in edge_results if row.get("status") == "created"]
    if not created:
        summary = {"label": label, "created": 0}
        print(f"[model] {summary}", flush=True)
        return summary

    edge_pct = [float(row["edge"].edge_pct or 0.0) for row in created]
    ev_values = []
    for row in created:
        edge = row["edge"]
        for value in (edge.ev_away, edge.ev_home, edge.ev_over, edge.ev_under):
            if value is not None:
                ev_values.append(float(value))

    summary = {
        "label": label,
        "created": len(created),
        "avg_edge_pct": round(_mean(edge_pct), 4),
        "max_edge_pct": round(max(edge_pct), 4),
        "avg_ev": round(_mean(ev_values), 4) if ev_values else None,
        "max_ev": round(max(ev_values), 4) if ev_values else None,
    }
    print(f"[model] {summary}", flush=True)
    return summary
