from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal
from app.models.schema import Game, GameOdds, Prediction
from app.services.edge_service import (
    _odds_freshness_debug,
    _odds_row_debug_payload,
    _pick_odds_snapshot_for_game,
)
from app.services.odds_service import SnapshotType


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Debug edge_service odds lookup for a given date.")
    parser.add_argument("--date", default=None, help="Game date in YYYY-MM-DD. Defaults to today in UTC.")
    parser.add_argument("--run-stage", default="daily_open", help="Run stage to evaluate.")
    parser.add_argument("--snapshot-type", default="open", choices=["open", "pregame", "live"])
    parser.add_argument("--fallback-policy", default="reuse_fresh_same_stage")
    parser.add_argument("--game-id", type=int, action="append", help="Optional game_id filter. May be passed multiple times.")
    return parser.parse_args()


def _serialize_prediction(prediction: Prediction | None) -> dict | None:
    if prediction is None:
        return None
    return {
        "prediction_id": prediction.prediction_id,
        "game_id": prediction.game_id,
        "run_stage": prediction.run_stage,
        "is_active": prediction.is_active,
        "created_at": prediction.created_at.isoformat() if prediction.created_at else None,
    }


def _serialize_game(game: Game) -> dict:
    return {
        "game_id": game.game_id,
        "game_date": game.game_date.isoformat(),
        "away_team": game.away_team,
        "home_team": game.home_team,
        "status": game.status,
        "start_time": game.start_time,
    }


def _serialize_odds_rows(rows: list[GameOdds]) -> list[dict]:
    payload = []
    for row in rows:
        payload.append(
            {
                "row": _odds_row_debug_payload(row, source="db"),
                "freshness": _odds_freshness_debug(row),
            }
        )
    return payload


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    target_date = date.fromisoformat(args.date) if args.date else datetime.utcnow().date()
    snapshot_type = SnapshotType(args.snapshot_type)

    db = SessionLocal()
    try:
        games_query = db.query(Game).filter(Game.game_date == target_date).order_by(Game.game_id.asc())
        if args.game_id:
            games_query = games_query.filter(Game.game_id.in_(args.game_id))
        games = games_query.all()

        print(
            json.dumps(
                {
                    "target_date": target_date.isoformat(),
                    "run_stage": args.run_stage,
                    "snapshot_type": snapshot_type.value,
                    "fallback_policy": args.fallback_policy,
                    "games": len(games),
                },
                indent=2,
            )
        )

        for game in games:
            prediction = (
                db.query(Prediction)
                .filter(
                    Prediction.game_id == game.game_id,
                    Prediction.run_stage == args.run_stage,
                    Prediction.is_active == True,  # noqa: E712
                )
                .order_by(Prediction.created_at.desc(), Prediction.prediction_id.desc())
                .first()
            )
            odds_rows = (
                db.query(GameOdds)
                .filter(
                    GameOdds.game_id == game.game_id,
                    GameOdds.snapshot_type == snapshot_type,
                )
                .order_by(GameOdds.fetched_at.desc(), GameOdds.id.desc())
                .all()
            )
            selected, reason = _pick_odds_snapshot_for_game(
                db,
                game_id=game.game_id,
                snapshot_type=snapshot_type,
                explicit_odds=None,
                run_stage=args.run_stage,
                fallback_policy=args.fallback_policy,
            )

            print(
                json.dumps(
                    {
                        "game": _serialize_game(game),
                        "prediction": _serialize_prediction(prediction),
                        "odds_rows": _serialize_odds_rows(odds_rows),
                        "selected_odds": _odds_row_debug_payload(selected, source="selected") if selected else None,
                        "selected_freshness": _odds_freshness_debug(selected) if selected else None,
                        "final_skip_reason": reason,
                    },
                    indent=2,
                )
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
