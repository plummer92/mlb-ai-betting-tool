"""Offline, same-time export walk-forward. No app config/database/network imports.

Source snapshots are mutable legacy exports: new fits are out-of-sample, but
historical feature availability and original-model selection remain unverified.
"""
import hashlib
import json
from pathlib import Path
from datetime import timedelta

import numpy as np
import pandas as pd
from scipy.stats import norm

from app.services.shadow_math import proper_scores
from app.services.totals_shadow_math import fit_totals, totals_probability
from analysis.season_calibration import no_vig


def build_cohort(data):
    games, preds, odds, reviews = [data[name].copy() for name in ("games", "predictions", "game_odds", "game_outcomes_review")]
    conflicts = reviews.merge(games, on="game_id", suffixes=("_review", "_game"))
    conflicts = conflicts[(conflicts.final_home_score_review != conflicts.final_home_score_game) |
                          (conflicts.final_away_score_review != conflicts.final_away_score_game)]
    games = games[games.status.isin(["Final", "Game Over", "Completed Early"]) & games.final_home_score.notna() &
        games.final_away_score.notna() & (games.final_home_score+games.final_away_score > 0) & ~games.game_id.isin(conflicts.game_id)]
    games["start"] = pd.to_datetime(games.start_time, utc=True, format="mixed", errors="coerce")
    preds = preds.merge(games[["game_id", "start", "final_home_score", "final_away_score"]], on="game_id")
    preds["created"] = pd.to_datetime(preds.created_at, utc=True, format="mixed", errors="coerce")
    preds = preds[preds.created < preds.start].sort_values(["created", "prediction_id"]).drop_duplicates("game_id", keep="last")
    odds["fetched"] = pd.to_datetime(odds.fetched_at, utc=True, format="mixed", errors="coerce")
    odds = odds.dropna(subset=["total_line", "over_odds", "under_odds", "fetched"])
    odds = odds[(odds.over_odds.abs() >= 100) & (odds.under_odds.abs() >= 100)]
    grouped = {gid: group for gid, group in odds.groupby("game_id")}
    result = []
    for pred in preds.itertuples():
        prices = grouped.get(pred.game_id)
        if prices is None:
            continue
        prices = prices[(prices.fetched <= pred.created) & (prices.fetched < pred.start)]
        if prices.empty:
            continue
        price = prices.sort_values(["fetched", "sportsbook", "id"]).iloc[-1]
        total = pred.final_home_score+pred.final_away_score
        result.append(dict(game_id=int(pred.game_id), created_at=pred.created.to_pydatetime(),
            scheduled_start=pred.start.to_pydatetime(),
            # Export has no immutable final observation clock. A one-day boundary
            # embargo is used below; this is historical research, not gate evidence.
            observed_at=pred.start.to_pydatetime()+timedelta(hours=8),
            projected_total=float(pred.projected_total), total=int(total), line=float(price.total_line),
            market_p=float(no_vig(price.over_odds, price.under_odds)),
            model_p=float(norm.sf(price.total_line, loc=pred.projected_total, scale=2.5)),
            sportsbook=price.sportsbook, price_id=int(price.id), prediction_id=int(pred.prediction_id),
            price_time=price.fetched.isoformat(), month=pred.start.strftime("%Y-%m"),
            date=pred.start.date().isoformat(), y=int(total > price.total_line), push=bool(total == price.total_line)))
    return result


def walk_forward_totals(rows):
    results, fits = [], []
    for month in sorted({r["month"] for r in rows}):
        boundary = pd.Timestamp(month+"-01", tz="UTC").to_pydatetime()-timedelta(days=1)
        seasonal, no_factor = [fit_totals(rows, boundary, seasonal_factor=f) for f in (True, False)]
        if seasonal is None or no_factor is None:
            continue
        fits.append(dict(month=month, seasonal=seasonal, no_factor=no_factor))
        train_ids = {r["game_id"] for r in rows if r["created_at"] < boundary and r["observed_at"] < boundary and r["scheduled_start"] < boundary}
        for row in rows:
            if row["month"] != month:
                continue
            assert row["game_id"] not in train_ids
            a, b = [totals_probability(fit, row["projected_total"], row["line"], row["scheduled_start"]) for fit in (seasonal, no_factor)]
            results.append({**row, "nb_factor":a["probability"], "nb_no_factor":b["probability"],
                "push_factor":a["push_probability"], "push_no_factor":b["push_probability"],
                "train_n":seasonal["train_n"], "train_cutoff":boundary.isoformat()})
    return results, fits


def metrics_and_reliability(rows):
    metrics, reliability = [], []
    scored = [r for r in rows if not r["push"]]
    for name in ("model_p", "market_p", "nb_factor", "nb_no_factor"):
        metrics.append(dict(candidate=name, **proper_scores([r["y"] for r in scored], [r[name] for r in scored]),
            n=len(scored), pushes_excluded=len(rows)-len(scored)))
        for lower in np.arange(0., 1., .1):
            group = [r for r in scored if lower <= r[name] < lower+.1]
            if group:
                reliability.append(dict(candidate=name, bin=f"{lower:.1f}-{lower+.1:.1f}", n=len(group),
                    mean_probability=float(np.mean([r[name] for r in group])),
                    actual_frequency=float(np.mean([r["y"] for r in group]))))
    return metrics, reliability


def main():
    root = Path("_season_export")
    names = ("games", "predictions", "game_odds", "game_outcomes_review")
    data = {name:pd.read_csv(root/f"{name}.csv", encoding="utf-8") for name in names}
    cohort = build_cohort(data)
    rows, fits = walk_forward_totals(cohort)
    metrics, reliability = metrics_and_reliability(rows)
    out = Path("analysis")
    pd.DataFrame(rows).to_csv(out/"totals_shadow_oos.csv", index=False, encoding="utf-8")
    pd.DataFrame(metrics).to_csv(out/"totals_shadow_metrics.csv", index=False, encoding="utf-8")
    pd.DataFrame(reliability).to_csv(out/"totals_shadow_reliability.csv", index=False, encoding="utf-8")
    audit = dict(cohort_games=len(cohort), oos_games=len(rows), folds=fits,
        source_hashes={n:hashlib.sha256((root/f"{n}.csv").read_bytes()).hexdigest() for n in names},
        limits="Monthly earlier-data fits; one-day embargo; prices no later than prediction. Legacy feature availability unverified; no prospective gate evidence.")
    (out/"totals_shadow_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(pd.DataFrame(metrics).to_string(index=False))


if __name__ == "__main__":
    main()
