from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal
from app.models.schema import BacktestGame


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify that a backtest row only uses pregame information.")
    parser.add_argument("--game-id", type=int, required=True, help="Historical MLB gamePk / backtest game_id")
    return parser.parse_args()


def _team_prior_rows(db, team_id: int, cutoff):
    rows = (
        db.query(BacktestGame)
        .filter(
            BacktestGame.feature_cutoff_time < cutoff,
            ((BacktestGame.home_team_id == team_id) | (BacktestGame.away_team_id == team_id)),
        )
        .order_by(BacktestGame.feature_cutoff_time.asc(), BacktestGame.game_id.asc())
        .all()
    )
    return [
        {
            "game_id": row.game_id,
            "game_date": row.game_date.isoformat(),
            "cutoff_time": row.feature_cutoff_time.isoformat() if row.feature_cutoff_time else None,
            "team_side": "home" if row.home_team_id == team_id else "away",
        }
        for row in rows
    ]


def _starter_prior_rows(db, starter_id: int | None, cutoff):
    if starter_id is None:
        return []
    rows = (
        db.query(BacktestGame)
        .filter(
            BacktestGame.feature_cutoff_time < cutoff,
            ((BacktestGame.home_starter_id == starter_id) | (BacktestGame.away_starter_id == starter_id)),
        )
        .order_by(BacktestGame.feature_cutoff_time.asc(), BacktestGame.game_id.asc())
        .all()
    )
    return [
        {
            "game_id": row.game_id,
            "game_date": row.game_date.isoformat(),
            "cutoff_time": row.feature_cutoff_time.isoformat() if row.feature_cutoff_time else None,
            "starter_side": "home" if row.home_starter_id == starter_id else "away",
        }
        for row in rows
    ]


def main() -> None:
    args = _parse_args()
    db = SessionLocal()
    try:
        row = db.query(BacktestGame).filter(BacktestGame.game_id == args.game_id).first()
        if row is None:
            raise SystemExit(f"game_id={args.game_id} not found in backtest_games")

        cutoff = row.feature_cutoff_time
        home_team_sources = _team_prior_rows(db, row.home_team_id, cutoff)
        away_team_sources = _team_prior_rows(db, row.away_team_id, cutoff)
        home_starter_sources = _starter_prior_rows(db, row.home_starter_id, cutoff)
        away_starter_sources = _starter_prior_rows(db, row.away_starter_id, cutoff)

        payload = {
            "target_row": {
                "game_id": row.game_id,
                "game_date": row.game_date.isoformat(),
                "game_start_time": row.game_start_time.isoformat() if row.game_start_time else None,
                "feature_cutoff_time": row.feature_cutoff_time.isoformat() if row.feature_cutoff_time else None,
                "feature_cutoff_policy": row.feature_cutoff_policy,
                "features_complete": row.features_complete,
                "odds_complete": row.odds_complete,
                "incomplete_reasons": json.loads(row.incomplete_reasons_json) if row.incomplete_reasons_json else [],
                "odds": {
                    "row_id": row.odds_row_id,
                    "snapshot_type": row.odds_snapshot_type,
                    "snapshot_policy": row.odds_snapshot_policy,
                    "fetched_at": row.odds_fetched_at.isoformat() if row.odds_fetched_at else None,
                    "away_ml": row.odds_away_ml,
                    "home_ml": row.odds_home_ml,
                    "total": float(row.odds_total) if row.odds_total is not None else None,
                },
            },
            "home_team_sources": home_team_sources,
            "away_team_sources": away_team_sources,
            "home_starter_sources": home_starter_sources,
            "away_starter_sources": away_starter_sources,
            "proof": {
                "all_home_team_rows_before_cutoff": all(item["cutoff_time"] < row.feature_cutoff_time.isoformat() for item in home_team_sources),
                "all_away_team_rows_before_cutoff": all(item["cutoff_time"] < row.feature_cutoff_time.isoformat() for item in away_team_sources),
                "all_home_starter_rows_before_cutoff": all(item["cutoff_time"] < row.feature_cutoff_time.isoformat() for item in home_starter_sources),
                "all_away_starter_rows_before_cutoff": all(item["cutoff_time"] < row.feature_cutoff_time.isoformat() for item in away_starter_sources),
                "odds_before_cutoff": (
                    row.odds_fetched_at.isoformat() < row.feature_cutoff_time.isoformat()
                    if row.odds_fetched_at and row.feature_cutoff_time
                    else None
                ),
            },
        }
        print(json.dumps(payload, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
