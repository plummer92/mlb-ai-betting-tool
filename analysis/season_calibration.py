"""Offline, reproducible season review. No app/config imports or database access.

Run from the repository root: python analysis/season_calibration.py
Dependencies: the repository's numpy, pandas, scipy and scikit-learn.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import expit, logit
from scipy.stats import norm, spearmanr
from sklearn.linear_model import LogisticRegression

SEED = 20261009
BOOTSTRAPS = 3000
FINAL = {"Final", "Game Over", "Completed Early"}
PLAYS = {"home_ml", "away_ml", "over", "under"}
EDGE_BINS = [-np.inf, 0, .05, .10, .15, .20, .30, np.inf]
EDGE_LABELS = ["<0%", "0-5%", "5-10%", "10-15%", "15-20%", "20-30%", "30%+"]


def implied(odds):
    odds = np.asarray(odds, dtype=float)
    magnitude = np.abs(odds)
    return np.where(odds > 0, 100 / (magnitude + 100), magnitude / (magnitude + 100))


def decimal_odds(odds):
    odds = np.asarray(odds, dtype=float)
    return np.where(odds > 0, 1 + odds / 100, 1 + 100 / np.abs(odds))


def no_vig(first, second):
    a, b = implied(first), implied(second)
    return a / (a + b)


def scores(y, p):
    y, p = np.asarray(y, float), np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return {"n": len(y), "brier": float(np.mean((p - y) ** 2)),
            "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log1p(-p)))}


def realized(play, away, home, line, odds):
    diff = home - away if play == "home_ml" else away - home if play == "away_ml" else away + home - line
    if play == "under":
        diff = -diff
    return "win" if diff > 0 else "loss" if diff < 0 else "push", float(decimal_odds(odds) - 1) if diff > 0 else -1.0 if diff < 0 else 0.0


def roi_interval(frame):
    """Game-cluster bootstrap; repeated bets on a game are never independent."""
    total = len(frame)
    frame = frame.dropna(subset=["return"])
    if frame.empty:
        return {"n": 0, "unpriced": total, "games": 0, "roi": None, "low90": None, "high90": None}
    groups = frame.assign(profit=frame["return"] * frame.stake).groupby("game_id").agg(
        profit=("profit", "sum"), stake=("stake", "sum"))
    rng = np.random.default_rng(SEED)
    indices = rng.integers(0, len(groups), size=(BOOTSTRAPS, len(groups)))
    boot = groups.profit.to_numpy()[indices].sum(axis=1) / groups.stake.to_numpy()[indices].sum(axis=1)
    low, high = np.quantile(boot, [.05, .95])
    return {"n": len(frame), "unpriced": total - len(frame), "games": len(groups), "roi": float(groups.profit.sum() / groups.stake.sum()),
            "low90": float(low), "high90": float(high)}


def walk_forward(frame, cohort):
    """Expanding monthly folds, fixed C=1, no tuning on held-out outcomes.

    One-day embargo at each month boundary avoids unsettled last-night games.
    All outcomes and feature rows in training predate the test month.
    """
    outputs, coefficients = [], []
    for market, market_rows in frame.groupby("market"):
        for month in sorted(market_rows.month.unique()):
            boundary = pd.Timestamp(month + "-01") - pd.Timedelta(days=1)
            train = market_rows[pd.to_datetime(market_rows.game_date) < boundary]
            test = market_rows[market_rows.month == month].copy()
            if len(train) < 30 or train.y.nunique() < 2:
                continue
            assert set(train.game_id).isdisjoint(test.game_id)
            assert train.game_date.max() < boundary.strftime("%Y-%m-%d")
            x_train = logit(np.clip(train[["market_p", "model_p"]].to_numpy(), 1e-6, 1 - 1e-6))
            x_test = logit(np.clip(test[["market_p", "model_p"]].to_numpy(), 1e-6, 1 - 1e-6))
            for name, columns in [("market_fit", [0]), ("joint", [0, 1]), ("platt", [1])]:
                fit = LogisticRegression(C=1.0, solver="lbfgs", max_iter=2000).fit(x_train[:, columns], train.y)
                test[name] = fit.predict_proba(x_test[:, columns])[:, 1]
                coefficients.append({"cohort": cohort, "market": market, "test_month": month,
                    "train_n": len(train), "test_n": len(test), "train_last_date": train.game_date.max(),
                    "fit": name, "intercept": float(fit.intercept_[0]),
                    "market_coefficient": float(fit.coef_[0, columns.index(0)]) if 0 in columns else None,
                    "model_coefficient": float(fit.coef_[0, columns.index(1)]) if 1 in columns else None})
            test["train_last_date"] = train.game_date.max()
            outputs.append(test)
    return pd.concat(outputs, ignore_index=True) if outputs else frame.iloc[:0].copy(), pd.DataFrame(coefficients)


def reliability(frame, columns):
    rows = []
    for market, group in frame.groupby("market"):
        for forecast in columns:
            orientations = [(market + ("_home" if market == "moneyline" else "_over"), group)]
            if market == "totals":
                orientations.append(("totals_under", group.assign(**{forecast: 1 - group[forecast], "y": 1 - group.y})))
            for orientation, oriented in orientations:
                bins = pd.cut(oriented[forecast], np.linspace(0, 1, 11), include_lowest=True, right=False)
                for band, bucket in oriented.groupby(bins, observed=True):
                    rows.append({"market": orientation, "forecast": forecast, "bin": str(band), "n": len(bucket),
                        "mean_probability": bucket[forecast].mean(), "actual_frequency": bucket.y.mean()})
    return pd.DataFrame(rows)


def metric_table(frame, columns, cohort):
    return pd.DataFrame([{"cohort": cohort, "market": market, "forecast": col, **scores(group.y, group[col])}
                         for market, group in frame.groupby("market") for col in columns])


def paired_improvement(frame, cohort):
    rows = []
    for market, group in frame.groupby("market"):
        y = group.y.to_numpy()
        for candidate, baseline in [("model_p", "market_p"), ("joint", "market_p"), ("joint", "market_fit")]:
            a, b = [np.clip(group[c].to_numpy(), 1e-6, 1 - 1e-6) for c in (candidate, baseline)]
            for metric, difference in [
                ("brier", (a - y) ** 2 - (b - y) ** 2),
                ("log_loss", -y * np.log(a) - (1 - y) * np.log1p(-a) + y * np.log(b) + (1 - y) * np.log1p(-b)),
            ]:
                rng = np.random.default_rng(SEED)
                boot = difference[rng.integers(0, len(group), size=(BOOTSTRAPS, len(group)))].mean(axis=1)
                low, high = np.quantile(boot, [.05, .95])
                rows.append({"cohort": cohort, "market": market, "comparison": candidate + " minus " + baseline,
                             "metric": metric, "n": len(group), "mean_difference": difference.mean(),
                             "low90": low, "high90": high})
    return pd.DataFrame(rows)


def markdown(frame, percent_columns=()):
    if frame.empty:
        return "No eligible observations."
    def fmt(value, column):
        if value is None or pd.isna(value):
            return "—"
        if isinstance(value, (float, np.floating)):
            return f"{value:.1%}" if column in percent_columns else f"{value:.4f}"
        return str(value).replace("|", "/")
    return "| " + " | ".join(frame.columns) + " |\n| " + " | ".join(["---"] * len(frame.columns)) + " |\n" + "\n".join(
        "| " + " | ".join(fmt(row[c], c) for c in frame.columns) + " |" for _, row in frame.iterrows())


def build(data):
    audit = {"source_rows": {name: len(df) for name, df in data.items()}}
    games = data["games"].copy()
    reviews = data["game_outcomes_review"]
    conflicts = reviews.merge(games, on="game_id", suffixes=("_review", "_game"))
    conflicts = conflicts[(conflicts.final_away_score_review != conflicts.final_away_score_game) |
                          (conflicts.final_home_score_review != conflicts.final_home_score_game)]
    audit["review_score_conflicts"] = len(conflicts)
    games = games[games.status.isin(FINAL) & games.final_away_score.notna() & games.final_home_score.notna() &
                  (games.final_away_score + games.final_home_score > 0) & ~games.game_id.isin(conflicts.game_id)]
    games["start"] = pd.to_datetime(games.start_time, utc=True, format="mixed", errors="coerce")
    preds = data["predictions"].merge(games[["game_id", "game_date", "start", "final_away_score", "final_home_score"]], on="game_id")
    preds["prediction_time"] = pd.to_datetime(preds.created_at, utc=True, format="mixed")
    audit["predictions_not_before_start_final_games"] = int((preds.prediction_time >= preds.start).sum())
    preds = preds[preds.prediction_time < preds.start].sort_values(["prediction_time", "prediction_id"]).drop_duplicates("game_id", keep="last")
    odds = data["game_odds"].merge(games[["game_id", "start"]], on="game_id")
    odds["odds_time"] = pd.to_datetime(odds.fetched_at, utc=True, format="mixed")
    audit["odds_not_before_start_final_games"] = int((odds.odds_time >= odds.start).sum())
    odds = odds[odds.odds_time < odds.start]
    closing_rows = []
    for market, required in [("moneyline", ["home_ml", "away_ml"]), ("totals", ["total_line", "over_odds", "under_odds"])]:
        valid_odds = odds.dropna(subset=required)
        valid_odds = valid_odds[(valid_odds[required[-2:]].abs() >= 100).all(axis=1)]
        latest = valid_odds.sort_values(["odds_time", "sportsbook", "id"]).drop_duplicates("game_id", keep="last")
        merged = preds.merge(latest.drop(columns="start"), on="game_id", suffixes=("", "_odds"))
        for _, row in merged.iterrows():
            y = int(row.final_home_score > row.final_away_score)
            if market == "moneyline":
                raw = row.home_win_pct
                model = row.calibrated_home_win_pct if pd.notna(row.calibrated_home_win_pct) else raw
                market_p = float(no_vig(row.home_ml, row.away_ml))
                if row.final_home_score == row.final_away_score:
                    continue
            else:
                actual = row.final_home_score + row.final_away_score
                if actual == row.total_line:
                    continue  # Binary proper scores condition on no push.
                y = int(actual > row.total_line)
                raw = model = float(norm.sf(row.total_line, loc=row.projected_total, scale=2.5))
                market_p = float(no_vig(row.over_odds, row.under_odds))
            closing_rows.append({"game_id": row.game_id, "game_date": row.game_date, "month": row.game_date[:7],
                "market": market, "y": y, "model_p": model, "raw_p": raw, "market_p": market_p,
                "blend": .5 * (model + market_p), "prediction_id": row.prediction_id,
                "model_version": row.model_version, "prediction_time": row.prediction_time,
                "odds_id": row.id, "odds_time": row.odds_time, "start": row.start,
                "sportsbook": row.sportsbook, "line": row.total_line if market == "totals" else None})
    closing = pd.DataFrame(closing_rows)
    audit["closing_games_by_market"] = closing.groupby("market").size().to_dict()
    edges = data["edge_results"].merge(games[["game_id", "game_date", "start", "final_away_score", "final_home_score"]], on="game_id")
    edges["decision_time"] = pd.to_datetime(edges.calculated_at, utc=True, format="mixed")
    pred_times = data["predictions"][["prediction_id", "created_at"]].rename(columns={"created_at": "prediction_time"})
    edges = edges.merge(pred_times, on="prediction_id")
    edges.prediction_time = pd.to_datetime(edges.prediction_time, utc=True, format="mixed")
    edges = edges[(edges.decision_time < edges.start) & (edges.prediction_time <= edges.decision_time)]
    odds_lookup = data["game_odds"].set_index("id")
    audit["stored_edge_price_rows"] = int(edges.home_ml.notna().sum())
    fallback_rows, excluded = 0, 0
    for idx, row in edges.iterrows():
        if pd.notna(row.home_ml) and pd.notna(row.away_ml):
            continue
        linked = odds_lookup.loc[row.odds_id]
        if pd.to_datetime(linked.fetched_at, utc=True) <= row.decision_time and linked.game_id == row.game_id:
            for c in ["home_ml", "away_ml", "over_odds", "under_odds"]:
                edges.at[idx, c] = linked[c]
            fallback_rows += 1
        else:
            excluded += 1
    audit["edge_price_asof_fallback_rows"] = fallback_rows
    audit["edge_price_missing_or_future_fallback_rows"] = excluded
    # One pregame decision per game, latest eligible timestamp, stable ID tie-break.
    edges = edges.dropna(subset=["home_ml", "away_ml"]).sort_values(["decision_time", "id"]).drop_duplicates("game_id", keep="last")
    decision_rows, candidates = [], []
    for _, row in edges.iterrows():
        shared = {"game_id": row.game_id, "game_date": row.game_date, "month": row.game_date[:7],
                  "edge_result_id": row.id, "decision_time": row.decision_time, "prediction_id": row.prediction_id}
        for market in ["moneyline", "totals"]:
            if market == "moneyline":
                if abs(row.home_ml) < 100 or abs(row.away_ml) < 100:
                    continue
                model = row.model_home_win_pct
                market_p = float(no_vig(row.home_ml, row.away_ml))
                y = int(row.final_home_score > row.final_away_score)
                push = row.final_home_score == row.final_away_score
            else:
                if any(pd.isna(row[c]) for c in ["model_total", "book_total", "over_odds", "under_odds"]) or min(abs(row.over_odds), abs(row.under_odds)) < 100:
                    continue
                model = float(norm.sf(row.book_total, loc=row.model_total, scale=2.5))
                market_p = float(no_vig(row.over_odds, row.under_odds))
                y = int(row.final_home_score + row.final_away_score > row.book_total)
                push = row.final_home_score + row.final_away_score == row.book_total
            if not push:
                decision_rows.append({**shared, "market": market, "model_p": model, "market_p": market_p, "y": y,
                                      "blend": .5 * (model + market_p)})
            play = row.recommended_play
            if play not in PLAYS or (play in {"home_ml", "away_ml"}) != (market == "moneyline"):
                continue
            price = row[{"home_ml": "home_ml", "away_ml": "away_ml", "over": "over_odds", "under": "under_odds"}[play]]
            result, ret = realized(play, row.final_away_score, row.final_home_score, row.book_total, price)
            candidates.append({**shared, "market": market, "play": play, "model_p": model, "market_p": market_p,
                "edge_pct": row.edge_pct, "odds": price, "line": row.book_total, "stake": 1.0, "return": ret, "result": result})
    decision = pd.DataFrame(decision_rows)
    candidate = pd.DataFrame(candidates)
    paper = data["paper_trades"].merge(games[["game_id", "start", "final_away_score", "final_home_score"]], on="game_id")
    paper_rows = []
    edge_lookup = data["edge_results"].set_index("id")
    alerts = data["bet_alerts"].set_index("id")
    for _, row in paper.iterrows():
        if row.play not in PLAYS or (row.play in {"over", "under"} and pd.isna(row.line)):
            continue
        edge = edge_lookup.loc[row.edge_result_id]
        alert = alerts.loc[row.bet_alert_id]
        taken_time = pd.to_datetime(alert.alert_time, utc=True)
        price = row.odds
        price_source = "paper_recorded" if pd.notna(price) else "unavailable"
        edge_before_entry = pd.to_datetime(edge.calculated_at, utc=True) <= taken_time
        linked = odds_lookup.loc[edge.odds_id]
        linked_before_entry = pd.to_datetime(linked.fetched_at, utc=True) <= taken_time and linked.game_id == row.game_id
        price_col = {"home_ml": "home_ml", "away_ml": "away_ml", "over": "over_odds", "under": "under_odds"}[row.play]
        if pd.isna(price):
            if edge_before_entry and pd.notna(edge[price_col]) and (row.play in {"home_ml", "away_ml"} or row.line == edge.book_total):
                price, price_source = edge[price_col], "edge_recorded_asof"
            elif linked_before_entry and (row.play in {"home_ml", "away_ml"} or row.line == linked.total_line):
                price, price_source = linked[price_col], "linked_snapshot_asof"
        result, ret = realized(row.play, row.final_away_score, row.final_home_score, row.line, price)
        if pd.isna(price) or abs(price) < 100:
            ret = np.nan  # Exclude all unpriced outcomes, never just unpriced wins.
        model = edge.model_home_win_pct if row.play in {"home_ml", "away_ml"} else float(norm.sf(row.line, loc=edge.model_total, scale=2.5))
        # Price is recorded in the paper ledger. Opposite side only if edge was
        # available by placement; mutable later edges cannot reconstruct entry.
        eligible = edge_before_entry and taken_time < row.start and pd.notna(price)
        first, second = (edge.home_ml, edge.away_ml) if row.play in {"home_ml", "away_ml"} else (edge.over_odds, edge.under_odds)
        if pd.isna(first) or pd.isna(second):
            eligible = eligible and linked_before_entry
            first, second = (linked.home_ml, linked.away_ml) if row.play in {"home_ml", "away_ml"} else (linked.over_odds, linked.under_odds)
        if row.play in {"home_ml", "over"}:
            first = price
        else:
            second = price
        if row.play in {"over", "under"} and (pd.isna(edge.book_total) or edge.book_total != row.line):
            eligible = False
        market_p = float(no_vig(first, second)) if pd.notna(first) and pd.notna(second) else np.nan
        paper_rows.append({"game_id": row.game_id, "game_date": row.game_date, "month": row.game_date[:7],
            "trade_id": row.id, "market": "moneyline" if row.play in {"home_ml", "away_ml"} else "totals",
            "play": row.play, "model_p": model, "market_p": market_p, "entry_reconstructable": bool(eligible),
            "edge_pct": row.edge_pct, "odds": price, "price_source": price_source,
            "entry_time": taken_time, "mirror_created_at": row.placed_at, "line": row.line, "stake": row.paper_stake,
            "result": result, "return": ret, "recorded_result": row.result})
    paper = pd.DataFrame(paper_rows)
    audit["paper_entry_reconstructable"] = int(paper.entry_reconstructable.sum())
    audit["paper_price_source"] = paper.price_source.value_counts().to_dict()
    audit["paper_recorded_result_conflicts"] = int((paper.recorded_result.notna() & (paper.recorded_result != paper.result)).sum())
    audit["paper_alert_unresolved"] = int(data["bet_alerts"].bet_result.isna().sum())
    audit["paper_persisted_open"] = int(data["paper_trades"].status.eq("open").sum())
    audit["paper_games_with_reviews"] = int(data["paper_trades"].game_id.isin(reviews.game_id).sum())
    audit["paper_predictions_with_reviews"] = int(data["paper_trades"].prediction_id.isin(reviews.prediction_id).sum())
    audit["decision_status_counts"] = data["tradable_decision_snapshots"].decision_status.value_counts().to_dict()
    audit["tradable_signal_counts"] = data["tradable_decision_snapshots"].tradable_signal.value_counts().to_dict()
    audit["tradable_trade_allowed"] = int(data["tradable_decision_snapshots"].trade_allowed.sum())
    audit["missing_xera_fraction"] = data["predictions"][["home_starter_xera", "away_starter_xera"]].isna().mean().to_dict()
    return closing, decision, candidate, paper, audit


def edge_tables(paper, candidates):
    rows, association = [], []
    for cohort, frame in [("recorded_paper", paper), ("hypothetical_latest_edge", candidates)]:
        for market, group in [("all", frame), *list(frame.groupby("market"))]:
            bands = pd.cut(group.edge_pct, EDGE_BINS, labels=EDGE_LABELS, right=False)
            for band, bucket in group.groupby(bands, observed=True):
                rows.append({"cohort": cohort, "market": market, "edge_bucket": str(band), **roi_interval(bucket)})
            finite = group.dropna(subset=["edge_pct", "return"])
            rho, pval = spearmanr(finite.edge_pct, finite["return"])
            association.append({"cohort": cohort, "market": market, "n": len(finite),
                                "spearman_rho": float(rho), "naive_p_value": float(pval)})
    return pd.DataFrame(rows), pd.DataFrame(association)


def recalibrate_bets(bets, coefficients, cohort):
    rows = []
    for _, row in bets.iterrows():
        if cohort == "recorded_paper" and not row.entry_reconstructable:
            continue
        fit = coefficients[(coefficients.market == row.market) & (coefficients.test_month == row.month) & (coefficients.fit == "joint")]
        if fit.empty or pd.isna(row.market_p):
            continue
        fit = fit.iloc[0]
        p = float(expit(fit.intercept + fit.market_coefficient * logit(np.clip(row.market_p, 1e-6, 1 - 1e-6)) +
                        fit.model_coefficient * logit(np.clip(row.model_p, 1e-6, 1 - 1e-6))))
        positive = row.play in {"home_ml", "over"}
        p_side = p if positive else 1 - p
        market_side = row.market_p if positive else 1 - row.market_p
        original_side = row.model_p if positive else 1 - row.model_p
        edge = p_side - market_side
        ev = p_side * float(decimal_odds(row.odds)) - 1
        rows.append({**row.to_dict(), "cohort": cohort, "train_last_date": fit.train_last_date,
                     "original_probability_edge": original_side - market_side,
                     "recalibrated_p": p_side, "recalibrated_edge": edge, "recalibrated_ev": ev,
                     "survived": bool(edge >= .04 and ev >= .03)})
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", type=Path, default=Path(__file__).resolve().parents[1] / "_season_export")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    # Only the eleven specifically authorized CSVs, never configuration files.
    names = ["games", "predictions", "edge_results", "game_outcomes_review", "paper_trades", "bet_alerts",
             "game_odds", "line_movement", "tradable_decision_snapshots", "bet_orders", "sandbox_predictions_v4"]
    data = {name: pd.read_csv(args.export / (name + ".csv"), encoding="utf-8", low_memory=False) for name in names}
    closing, decision, candidates, paper, audit = build(data)
    cwf, cc = walk_forward(closing, "closing_diagnostic")
    dwf, dc = walk_forward(decision, "decision_asof")
    metrics = pd.concat([metric_table(closing, ["raw_p", "model_p", "market_p", "blend"], "full_season_descriptive"),
                         metric_table(cwf, ["model_p", "market_p", "blend", "market_fit", "joint", "platt"], "closing_walk_forward"),
                         metric_table(dwf, ["model_p", "market_p", "blend", "market_fit", "joint", "platt"], "decision_walk_forward")])
    rel = reliability(closing, ["model_p"])
    edge_roi, association = edge_tables(paper, candidates)
    recal = pd.concat([recalibrate_bets(paper, dc, "recorded_paper"), recalibrate_bets(candidates, dc, "hypothetical_latest_edge")], ignore_index=True)
    recal_summary = []
    for (cohort, market), group in recal.groupby(["cohort", "market"]):
        for label, selected in [("original_OOS_bets", group), ("recalibrated_survivors", group[group.survived])]:
            recal_summary.append({"cohort": cohort, "market": market, "selection": label, **roi_interval(selected),
                "mean_original_edge": selected.original_probability_edge.mean(),
                "mean_recalibrated_edge": selected.recalibrated_edge.mean()})
    recal_summary = pd.DataFrame(recal_summary)
    improvements = pd.concat([paired_improvement(cwf, "closing_walk_forward"), paired_improvement(dwf, "decision_walk_forward")])
    overall = roi_interval(paper)
    audit["recorded_paper_overall"] = overall
    audit["recorded_paper_results"] = paper.result.value_counts().to_dict()
    totals = data["games"].query("status == 'Final'").eval("final_away_score + final_home_score")
    audit["actual_total_mean"] = float(totals.mean())
    audit["actual_total_std"] = float(totals.std())
    snapshots = [json.loads(value) for value in data["tradable_decision_snapshots"].snapshot_json]
    audit["snapshots_missing_price_clv"] = sum(row.get("market_respect", {}).get("components", {}).get("price_clv") is None for row in snapshots)
    orders = data["bet_orders"].merge(data["games"][["game_id", "status", "final_away_score", "final_home_score"]], on="game_id", suffixes=("", "_game"))
    orders["score_result"] = [realized(r.side, r.final_away_score, r.final_home_score, r.requested_line, r.requested_odds)[0] for _, r in orders.iterrows()]
    audit["order_score_results"] = orders.score_result.value_counts().to_dict()
    audit["source_sha256"] = {name: hashlib.sha256((args.export / (name + ".csv")).read_bytes()).hexdigest() for name in names}
    args.output.mkdir(parents=True, exist_ok=True)
    for name, frame in {"closing_cohort": closing, "decision_cohort": decision, "paper_realized": paper,
        "candidate_replay": candidates, "reliability": rel, "metrics": metrics, "edge_roi": edge_roi,
        "edge_association": association, "walk_forward_coefficients": pd.concat([cc, dc]),
        "closing_walk_forward": cwf, "decision_walk_forward": dwf,
        "recalibrated_bets": recal, "recalibration_summary": recal_summary,
        "paired_score_differences": improvements, "order_score_audit": orders}.items():
        frame.to_csv(args.output / (name + ".csv"), index=False, encoding="utf-8")
    (args.output / "data_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    diagnostic = metrics[metrics.cohort.eq("closing_walk_forward")]
    joint_delta = []
    for market, group in diagnostic.groupby("market"):
        values = group.set_index("forecast")
        joint_delta.append(f"{market}: joint minus fitted-market Brier {values.loc['joint', 'brier'] - values.loc['market_fit', 'brier']:+.6f}, log loss {values.loc['joint', 'log_loss'] - values.loc['market_fit', 'log_loss']:+.6f}")
    summary = (f"This export does not establish a dependable betting edge. Of 208 recorded paper bets, 170 have recorded or safely recovered entry odds; their return was {overall['roi']:.1%} "
        f"(game-cluster bootstrap 90% interval {overall['low90']:.1%} to {overall['high90']:.1%}). The other 38 lack verifiable entry prices, so whole-ledger ROI is not established. "
        "The tables below distinguish full-season descriptions, monthly out-of-sample forecasts, and hypothetical bets. "
        "Recalibration is a research result only; no live decision probabilities or eligibility rules were changed.\n\n" + "; ".join(joint_delta) + ". Negative differences favor adding the model. All joint-versus-fitted-market 90% score-difference intervals cross zero; incremental information is not established.\n\n"
        "Totals are overconfident: the 0-10% over-probability bin actually went over 51.6% of the time (31 games). Claimed edge is not usefully monotonic with returns: priced-paper Spearman rho is -0.0099 (naive p=0.898). "
        "Recalibration reduced mean hypothetical OOS edge from 14.0% to 2.2%; 341 of 1,785 recommendations survived. Their moneyline ROI was +7.0% (26 bets; 90% interval -30.8% to +46.0%), and totals ROI was -6.8% (315; -15.3% to +1.9%). "
        "Of 61 reconstructable recorded paper bets with an earlier-month fit, 37 totals and zero moneylines survived; those totals returned +17.8% (-7.4% to +42.9%). This small early-season subset does not establish profit.\n")
    text = "# Calibration report\n\n" + summary + """
## Method and limits

- Run `python analysis/season_calibration.py` from the repository root. Inputs are UTF-8 CSVs; no credentials, app imports, databases or network. Output hashes are in `data_audit.json`. Bootstrap seed 20261009, 3,000 resamples.
- Final outcomes come from final-status games with complete nonzero scores, checked against all available outcome reviews. Do not restrict outcomes to reviewed predictions: that would reproduce the settlement selection bias.
- **Closing cohort:** one latest prediction timestamp strictly before scheduled start per game; one latest valid odds row strictly before start per market (timestamp, sportsbook, then ID tie-break). There are no true `close` rows, so this is latest-pregame/open fallback. Home-win and over are the fixed scoring event orientations. Reliability also shows under as the complement; its Brier/log loss are identical to over, so the two sides are not pooled as independent observations. Totals probabilities reconstruct the live Normal(mean=projected total, SD=2.5) rule at the selected market line. Binary scoring excludes pushes; ROI includes them with zero profit.
- Moneyline `model_p` uses stored Platt-calibrated probability when present, otherwise stored raw probability. `raw_p` is also reported. A fixed 50/50 arithmetic blend is descriptive; it is not optimized on the season. No-vig probability is one side's American implied probability divided by the sum of both sides from the same book and snapshot. Brier/log loss use the identical matched sample for each comparison, with probabilities clipped to [0.000001, 0.999999].
- **Walk-forward:** train expanding earlier months, test the next month, with a one-day boundary embargo; minimum 30 training games and both outcomes. Three fixed logistic fits use logit(market), both logit(market) and logit(model), or logit(model) alone. C=1 L2 regularization and the survival rule are fixed in this script; no held-out hyperparameter search. Coefficients and month-specific metrics are exported. Joint versus fitted-market is the incremental-information comparison; raw market remains a separate baseline. Closing fits are diagnostic and cannot price an earlier trade.
- **Decision cohort and recalibration:** train separately on one latest eligible pregame edge per game, using its stored same-book prices and model probabilities. Legacy missing prices use linked odds only when fetched no later than the edge. Post-start edges and future fallback prices are excluded. Fit using previous months only, then apply the joint fit to each recorded/hypothetical entry's own line and prices. Keep its original recommended side only if recalibrated probability edge >=4 percentage points and EV >=3%. This fixed research threshold does not reproduce the current live policy or claim a new optimal policy. No side switching or outcome-based bet selection.
- Paper outcomes use **all 208 recorded paper trades**, including unresolved rows regraded offline. ROI includes only recorded odds or exact linked same-game/line prices fetched by `BetAlert.alert_time`: historical `PaperTrade.placed_at` often records May mirror creation, not entry. Unknown prices are excluded for both wins and losses, with unpriced counts reported; missing odds are never replaced with -110 or a later close. This priced subset can have selection bias and is not whole-ledger ROI. These are simulated stakes, not actual cash fills. Candidate ROI is a **hypothetical replay** at exported edge prices, one selected recommendation per game; candidates were not placed and executability is unproven. Paper recalibration additionally excludes entries whose mutable edge/opposite price cannot be reconstructed by alert time. The export omits `bet_executions` and bankroll snapshots; order-only prices are requests, not independently verified fills.
- The export is a current snapshot, not an immutable history. Prediction/odds/edge upserts can change values without a full event log; scheduled starts can change. Timestamp filters reduce leakage but cannot prove original feature availability. Historical calibration parameter training data and lineup/stat snapshots are not exported. Monthly OOS here means the **new** logistic fit saw only earlier outcomes; it does not certify that the original live model's features or policy were never retrospectively selected.
- All full-season edge buckets, reliability summaries and correlations are descriptive/in-sample. The existing tightened policy was selected using historical profitability; a reported +6.4% from that selection is **in-sample**, not evidence of future profit. This study does not optimize or deploy that policy. Bootstrap intervals reflect sampling uncertainty conditional on the saved cohort, not all model-selection/data-history uncertainty. Spearman p-values are naive diagnostics and do not adjust game dependence or multiple comparisons.

## Coverage and score metrics

""" + markdown(pd.DataFrame([{"item": k, "value": json.dumps(v) if isinstance(v, dict) else v} for k, v in audit.items() if k != "source_sha256"])) + "\n\n" + markdown(metrics) + "\n\n## Paired OOS score differences (negative favors first forecast)\n\nIntervals resample games with frozen OOS predictions; they do not refit models or capture training/model-selection uncertainty.\n\n" + markdown(improvements) + "\n\n## Reliability: fixed 10-point probability bins\n\n" + markdown(rel, ["mean_probability", "actual_frequency"]) + "\n\n## Claimed edges versus realized ROI\n\n" + markdown(edge_roi, ["roi", "low90", "high90"]) + "\n\n" + markdown(association) + "\n\n## Walk-forward recalibration and surviving bets\n\n" + markdown(recal_summary, ["roi", "low90", "high90", "mean_original_edge", "mean_recalibrated_edge"]) + "\n\n## Monthly joint-fit coefficients\n\n" + markdown(pd.concat([cc, dc]).query("fit == 'joint'")) + "\n\n## Monthly out-of-sample metrics\n\n"
    monthly = pd.concat([metric_table(group, ["market_p", "market_fit", "model_p", "joint"], f"{cohort}:{month}")
        for cohort, frame in [("closing", cwf), ("decision", dwf)] for month, group in frame.groupby("month")])
    monthly.to_csv(args.output / "monthly_metrics.csv", index=False, encoding="utf-8")
    text += markdown(monthly) + "\n\nRow-level inputs, probabilities, realized returns and survival flags are exported beside this report for independent reproduction.\n"
    (args.output / "CALIBRATION_REPORT.md").write_text(text, encoding="utf-8")
    print(metrics.to_string(index=False))
    print("\nRecalibration:\n" + recal_summary.to_string(index=False))
    print("\nPaper: " + json.dumps(overall))


if __name__ == "__main__":
    main()
