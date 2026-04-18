from datetime import datetime
from zoneinfo import ZoneInfo

from app.db import SessionLocal
from app.models.schema import Game, Prediction
from app.services.backtest_service import build_live_feature_vector
from app.services.feature_builder import build_team_features
from app.services.mlb_api import fetch_bullpen_stats, fetch_pitcher_stats, fetch_team_stats
from app.services.statcast_service import fetch_team_statcast

ET = ZoneInfo("America/New_York")


def main() -> None:
    today = datetime.now(ET).date()
    db = SessionLocal()
    updated = 0
    skipped = 0
    errors: list[str] = []

    try:
        rows = (
            db.query(Prediction, Game)
            .join(Game, Game.game_id == Prediction.game_id)
            .filter(
                Game.game_date == today,
                Prediction.is_active == True,  # noqa: E712
            )
            .all()
        )

        for prediction, game in rows:
            if (
                prediction.kbb_adv is not None
                and prediction.park_factor_adv is not None
                and prediction.pythagorean_win_pct_adv is not None
            ):
                skipped += 1
                continue

            try:
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
                live_features = build_live_feature_vector(home_features, away_features)

                prediction.kbb_adv = live_features.get("kbb_adv")
                prediction.park_factor_adv = live_features.get("park_factor_adv")
                prediction.pythagorean_win_pct_adv = live_features.get("pythagorean_win_pct_adv")
                updated += 1
            except Exception as exc:
                errors.append(f"game_id={game.game_id} prediction_id={prediction.prediction_id} error={exc}")

        db.commit()
        print(
            {
                "date": str(today),
                "updated": updated,
                "skipped_already_populated": skipped,
                "errors": errors,
            }
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
