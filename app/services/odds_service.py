from datetime import datetime, timezone

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import THE_ODDS_API_KEY, THE_ODDS_API_URL
from app.models.schema import Game, GameOdds, LineMovement, SnapshotType
from app.services.ev_math import ml_to_implied_prob, prob_move, is_sharp_move


async def fetch_and_store_odds(
    db: Session,
    snapshot_type: SnapshotType,
    books: list[str] = ["draftkings"],
) -> list[GameOdds]:
    """
    Fetch odds and store with the given snapshot type.
    Skips games that already have this snapshot type stored (idempotent).
    """
    params = {
        "apiKey": THE_ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,totals",
        "oddsFormat": "american",
        "bookmakers": ",".join(books),
    }

    async with httpx.AsyncClient() as client:
        resp = await client.get(THE_ODDS_API_URL, params=params, timeout=10)
        resp.raise_for_status()
        raw_events = resp.json()

    stored = []
    skipped = 0

    for event in raw_events:
        game = _match_game(db, event)
        if not game:
            continue

        for bookmaker in event.get("bookmakers", []):
            odds_row = _parse_bookmaker(event, bookmaker, game.game_id, snapshot_type)
            if not odds_row:
                continue
            try:
                db.add(odds_row)
                db.flush()  # catch unique constraint before commit
                stored.append(odds_row)
            except IntegrityError:
                db.rollback()
                skipped += 1  # already have this snapshot, skip quietly

    db.commit()
    return stored


def compute_line_movement(
    db: Session,
    game_id: int,
    sportsbook: str = "draftkings",
) -> LineMovement | None:
    """
    Compare open vs pregame snapshots for a game and store the movement summary.
    Call this after the pregame snapshot is stored.
    """
    open_odds = (
        db.query(GameOdds)
        .filter(
            GameOdds.game_id == game_id,
            GameOdds.sportsbook == sportsbook,
            GameOdds.snapshot_type == SnapshotType.open,
        )
        .first()
    )
    pregame_odds = (
        db.query(GameOdds)
        .filter(
            GameOdds.game_id == game_id,
            GameOdds.sportsbook == sportsbook,
            GameOdds.snapshot_type == SnapshotType.pregame,
        )
        .first()
    )

    if not open_odds or not pregame_odds:
        return None  # can't compute without both snapshots

    away_move = prob_move(open_odds.away_ml, pregame_odds.away_ml)
    home_move = prob_move(open_odds.home_ml, pregame_odds.home_ml)
    total_move = float(pregame_odds.total_line or 0) - float(open_odds.total_line or 0)

    sharp_away, sharp_home = is_sharp_move(
        open_odds.away_ml, pregame_odds.away_ml,
        open_odds.home_ml, pregame_odds.home_ml,
    )

    total_steam_over = total_move >= 0.5
    total_steam_under = total_move <= -0.5

    movement = LineMovement(
        game_id=game_id,
        sportsbook=sportsbook,
        open_away_ml=open_odds.away_ml,
        open_home_ml=open_odds.home_ml,
        open_total=open_odds.total_line,
        pregame_away_ml=pregame_odds.away_ml,
        pregame_home_ml=pregame_odds.home_ml,
        pregame_total=pregame_odds.total_line,
        away_prob_move=round(away_move, 4),
        home_prob_move=round(home_move, 4),
        total_move=round(total_move, 1),
        sharp_away=sharp_away,
        sharp_home=sharp_home,
        total_steam_over=total_steam_over,
        total_steam_under=total_steam_under,
    )

    # Upsert — replace if already exists for this game
    existing = db.query(LineMovement).filter(LineMovement.game_id == game_id).first()
    if existing:
        for col in [
            "away_prob_move", "home_prob_move", "total_move",
            "sharp_away", "sharp_home", "total_steam_over", "total_steam_under",
            "pregame_away_ml", "pregame_home_ml", "pregame_total",
        ]:
            setattr(existing, col, getattr(movement, col))
        db.commit()
        return existing

    db.add(movement)
    db.commit()
    db.refresh(movement)
    return movement


# Static mapping from The Odds API team names → MLB Stats API team names.
# Handles multi-word nicknames (Red Sox, Blue Jays, White Sox) that break
# last-word matching, and tracks franchise relocations (Athletics → Sacramento).
_ODDS_TO_MLB: dict[str, str] = {
    "Arizona Diamondbacks":    "Arizona Diamondbacks",
    "Atlanta Braves":          "Atlanta Braves",
    "Baltimore Orioles":       "Baltimore Orioles",
    "Boston Red Sox":          "Boston Red Sox",
    "Chicago Cubs":            "Chicago Cubs",
    "Chicago White Sox":       "Chicago White Sox",
    "Cincinnati Reds":         "Cincinnati Reds",
    "Cleveland Guardians":     "Cleveland Guardians",
    "Colorado Rockies":        "Colorado Rockies",
    "Detroit Tigers":          "Detroit Tigers",
    "Houston Astros":          "Houston Astros",
    "Kansas City Royals":      "Kansas City Royals",
    "Los Angeles Angels":      "Los Angeles Angels",
    "Los Angeles Dodgers":     "Los Angeles Dodgers",
    "Miami Marlins":           "Miami Marlins",
    "Milwaukee Brewers":       "Milwaukee Brewers",
    "Minnesota Twins":         "Minnesota Twins",
    "New York Mets":           "New York Mets",
    "New York Yankees":        "New York Yankees",
    "Oakland Athletics":       "Oakland Athletics",
    "Philadelphia Phillies":   "Philadelphia Phillies",
    "Pittsburgh Pirates":      "Pittsburgh Pirates",
    "San Diego Padres":        "San Diego Padres",
    "San Francisco Giants":    "San Francisco Giants",
    "Seattle Mariners":        "Seattle Mariners",
    "St. Louis Cardinals":     "St. Louis Cardinals",
    "Tampa Bay Rays":          "Tampa Bay Rays",
    "Texas Rangers":           "Texas Rangers",
    "Toronto Blue Jays":       "Toronto Blue Jays",
    "Washington Nationals":    "Washington Nationals",
    # 2025 relocation variants that may appear in The Odds API
    "Athletics":               "Oakland Athletics",
    "Sacramento Athletics":    "Oakland Athletics",
}


def _match_game(db: Session, event: dict) -> Game | None:
    """
    Match an odds API event to a game in our DB using the static team-name
    lookup table. Falls back to a case-insensitive partial match if the name
    isn't in the map (handles future expansions / API naming drift).
    """
    raw_away = event.get("away_team", "")
    raw_home = event.get("home_team", "")
    away_name = _ODDS_TO_MLB.get(raw_away, raw_away)
    home_name = _ODDS_TO_MLB.get(raw_home, raw_home)
    today = datetime.now(timezone.utc).date()

    return (
        db.query(Game)
        .filter(
            Game.game_date == today,
            Game.away_team.ilike(away_name),
            Game.home_team.ilike(home_name),
        )
        .first()
    )


def _parse_bookmaker(
    event: dict,
    bookmaker: dict,
    game_id: int,
    snapshot_type: SnapshotType,
) -> GameOdds | None:
    row = GameOdds(
        game_id=game_id,
        sportsbook=bookmaker["key"],
        snapshot_type=snapshot_type,
        fetched_at=datetime.now(timezone.utc),
    )

    for market in bookmaker.get("markets", []):
        if market["key"] == "h2h":
            for o in market["outcomes"]:
                if o["name"] == event["away_team"]:
                    row.away_ml = int(o["price"])
                elif o["name"] == event["home_team"]:
                    row.home_ml = int(o["price"])
        elif market["key"] == "totals":
            for o in market["outcomes"]:
                if o["name"] == "Over":
                    row.total_line = o.get("point")
                    row.over_odds = int(o["price"])
                elif o["name"] == "Under":
                    row.under_odds = int(o["price"])

    if row.away_ml is None or row.home_ml is None:
        return None
    return row
