#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import desc

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal
from app.models.schema import Game, Prediction
from app.services.backtest_service import (
    apply_calibration,
    build_live_feature_vector,
    get_latest_calibration_result,
    score_logistic_home_probability,
)
from app.services.feature_builder import build_team_features
from app.services.mlb_api import (
    fetch_bullpen_stats,
    fetch_pitcher_stats,
    fetch_team_stats,
)
from app.services.odds_service import (
    SnapshotType,
    get_latest_odds_snapshot,
    get_market_home_probability,
    is_odds_snapshot_fresh,
)
from app.services.simulator import MODEL_VERSION, run_monte_carlo
from app.services.statcast_service import fetch_team_statcast


@dataclass
class FreshPrediction:
    game_id: int
    home_win_pct: float
    away_win_pct: float
    projected_total: float
    recommended_side: str | None
    model_version: str


def _parse_change_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _before_after_marker(
    prediction: Prediction,
    *,
    change_ts: datetime | None,
    change_model_version: str | None,
) -> str:
    if change_model_version and prediction.model_version == change_model_version:
        return "after_change"
    if change_ts is not None and prediction.created_at is not None:
        return "after_change" if prediction.created_at >= change_ts else "before_change"
    return "unknown"


def report_predictions(days: int, change_ts: datetime | None, change_model_version: str | None) -> None:
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = (
            db.query(Prediction, Game)
            .join(Game, Game.game_id == Prediction.game_id)
            .filter(
                Prediction.is_active == True,  # noqa: E712
                Prediction.created_at >= cutoff,
            )
            .order_by(desc(Prediction.created_at))
            .all()
        )

        print(
            "game_id,prediction_id,created_at,run_stage,model_version,"
            "home_win_pct,away_win_pct,game_date,before_after_change"
        )
        for prediction, game in rows:
            marker = _before_after_marker(
                prediction,
                change_ts=change_ts,
                change_model_version=change_model_version,
            )
            print(
                f"{prediction.game_id},{prediction.prediction_id},{prediction.created_at.isoformat()},"
                f"{prediction.run_stage},{prediction.model_version},{prediction.home_win_pct:.4f},"
                f"{prediction.away_win_pct:.4f},{game.game_date.isoformat()},{marker}"
            )

        favs = [max(float(p.home_win_pct), float(p.away_win_pct)) for p, _ in rows]
        print("")
        print(f"n={len(favs)}")
        if favs:
            print(f"avg_fav_prob={sum(favs) / len(favs):.4f}")
            print(f"gt_65={sum(1 for x in favs if x > 0.65)}")
            print(f"gt_70={sum(1 for x in favs if x > 0.70)}")
            print(f"gt_80={sum(1 for x in favs if x > 0.80)}")
    finally:
        db.close()


def _generate_fresh_prediction(db, game: Game) -> FreshPrediction:
    away_raw = fetch_team_stats(team_id=game.away_team_id, season=game.season)
    home_raw = fetch_team_stats(team_id=game.home_team_id, season=game.season)
    away_starter = fetch_pitcher_stats(game.away_pitcher_id, game.season, include_xera=True) if game.away_pitcher_id else None
    home_starter = fetch_pitcher_stats(game.home_pitcher_id, game.season, include_xera=True) if game.home_pitcher_id else None
    away_bullpen = fetch_bullpen_stats(game.away_team_id, game.season)
    home_bullpen = fetch_bullpen_stats(game.home_team_id, game.season)
    away_statcast = fetch_team_statcast(game.away_team_id, game.season)
    home_statcast = fetch_team_statcast(game.home_team_id, game.season)
    away_features = build_team_features(
        away_raw,
        starter_stats=away_starter,
        bullpen_stats=away_bullpen,
        statcast_team=away_statcast,
    )
    home_features = build_team_features(
        home_raw,
        starter_stats=home_starter,
        venue=game.venue,
        bullpen_stats=home_bullpen,
        statcast_team=home_statcast,
    )
    latest_open = get_latest_odds_snapshot(db, game_id=game.game_id, snapshot_type=SnapshotType.open)
    market_home_prob = get_market_home_probability(latest_open) if latest_open and is_odds_snapshot_fresh(latest_open) else None
    cal_result = get_latest_calibration_result(db)
    cal_params = json.loads(cal_result.calibration_params_json) if cal_result and cal_result.calibration_params_json else None
    logistic_home_prob = score_logistic_home_probability(
        build_live_feature_vector(home_features, away_features),
        cal_result,
    )
    result = run_monte_carlo(
        away_team=away_features,
        home_team=home_features,
        sim_count=1000,
        market_home_prob=market_home_prob,
        logistic_home_prob=logistic_home_prob,
    )
    home_win_pct = float(result["home_win_pct"])
    away_win_pct = float(result["away_win_pct"])
    if cal_params:
        home_win_pct, away_win_pct = apply_calibration(home_win_pct, away_win_pct, cal_params)
    return FreshPrediction(
        game_id=game.game_id,
        home_win_pct=float(home_win_pct),
        away_win_pct=float(away_win_pct),
        projected_total=float(result["projected_total"]),
        recommended_side=result["recommended_side"],
        model_version=MODEL_VERSION,
    )


def compare_today(run_stage: str) -> None:
    db = SessionLocal()
    try:
        today = datetime.now(timezone.utc).date()
        active_rows = {
            prediction.game_id: prediction
            for prediction, _game in (
                db.query(Prediction, Game)
                .join(Game, Game.game_id == Prediction.game_id)
                .filter(
                    Prediction.is_active == True,  # noqa: E712
                    Prediction.run_stage == run_stage,
                    Game.game_date == today,
                )
                .all()
            )
        }
        games = db.query(Game).filter(Game.game_date == today).order_by(Game.game_id).all()

        print(
            "game_id,active_prediction_id,active_created_at,active_model_version,"
            "active_home_win_pct,active_away_win_pct,fresh_model_version,"
            "fresh_home_win_pct,fresh_away_win_pct,delta_home,delta_away"
        )
        for game in games:
            fresh = _generate_fresh_prediction(db, game)
            active = active_rows.get(game.game_id)
            active_id = active.prediction_id if active else ""
            active_created_at = active.created_at.isoformat() if active and active.created_at else ""
            active_model_version = active.model_version if active else ""
            active_home = float(active.home_win_pct) if active else float("nan")
            active_away = float(active.away_win_pct) if active else float("nan")
            delta_home = fresh.home_win_pct - active_home if active else float("nan")
            delta_away = fresh.away_win_pct - active_away if active else float("nan")
            print(
                f"{game.game_id},{active_id},{active_created_at},{active_model_version},"
                f"{active_home:.4f},{active_away:.4f},{fresh.model_version},"
                f"{fresh.home_win_pct:.4f},{fresh.away_win_pct:.4f},"
                f"{delta_home:.4f},{delta_away:.4f}"
            )
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect and compare active MLB predictions.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser("report", help="Report active predictions in the last N days.")
    report_parser.add_argument("--days", type=int, default=7)
    report_parser.add_argument("--change-ts", default=None, help="UTC timestamp like 2026-04-02T17:30:00Z")
    report_parser.add_argument("--change-model-version", default=MODEL_VERSION)

    compare_parser = subparsers.add_parser("compare-today", help="Compare fresh current-code sims to active rows for today's games.")
    compare_parser.add_argument("--run-stage", default="daily_open")

    args = parser.parse_args()

    if args.command == "report":
        report_predictions(
            days=args.days,
            change_ts=_parse_change_ts(args.change_ts),
            change_model_version=args.change_model_version,
        )
        return

    if args.command == "compare-today":
        compare_today(run_stage=args.run_stage)
        return

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
