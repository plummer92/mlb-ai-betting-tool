from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from app.config import THE_ODDS_API_KEY, THE_ODDS_API_URL
from app.models.schema import Game, GameOdds, LineMovement, SnapshotType
from app.services.ev_math import is_sharp_move, ml_to_implied_prob, prob_move, remove_vig

ET = ZoneInfo("America/New_York")
DEFAULT_BOOKS = [
    "draftkings",
    "fanduel",
    "betmgm",
    "caesars",
    "espnbet",
    "betrivers",
]
ODDS_FRESHNESS_MINUTES: dict[SnapshotType, int] = {
    SnapshotType.open: 180,
    SnapshotType.pregame: 90,
    SnapshotType.live: 15,
}


@dataclass(frozen=True)
class _ConsensusSnapshot:
    away_ml: int
    home_ml: int
    total_line: float | None
    away_prob: float
    home_prob: float
    books: tuple[str, ...]


async def fetch_and_store_odds(
    db: Session,
    snapshot_type: SnapshotType,
    books: list[str] | None = None,
) -> list[GameOdds]:
    """
    Fetch odds and store with the given snapshot type.
    Stores at least one usable odds row per matched game when possible.
    If no new row is written for a matched game, reuses the latest fresh row
    for the same snapshot type within the freshness window.
    """
    requested_books = books or list(DEFAULT_BOOKS)
    params = {
        "apiKey": THE_ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,totals",
        "oddsFormat": "american",
        "bookmakers": ",".join(requested_books),
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(THE_ODDS_API_URL, params=params, timeout=10)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(_sanitize_http_error(exc)) from exc
        raw_events = resp.json()
        raw_size_bytes = len(resp.content or b"")

    print(
        f"[odds] fetch snapshot={snapshot_type.value} books={requested_books} "
        f"events_returned={len(raw_events)} mlb_events={len(raw_events)} raw_bytes={raw_size_bytes}"
    )

    stored: list[GameOdds] = []
    selected_rows: dict[int, GameOdds] = {}
    matched_game_ids: set[int] = set()
    matched_event_count = 0
    unmatched_events: list[str] = []
    skipped_inserts: list[str] = []
    inserted_rows: list[str] = []
    refreshed_rows: list[str] = []
    duplicate_fresh_reuse = 0

    for event in raw_events:
        game, match_reason = _match_game(db, event)
        if not game:
            unmatched_events.append(
                f"{event.get('away_team', '?')} @ {event.get('home_team', '?')} "
                f"(date={event.get('commence_time', '?')}, reason={match_reason})"
            )
            continue
        matched_event_count += 1
        matched_game_ids.add(game.game_id)

        valid_for_game = False
        bookmakers = _sorted_bookmakers(event, requested_books)
        if not bookmakers:
            unmatched_events.append(
                f"{event.get('away_team', '?')} @ {event.get('home_team', '?')} "
                f"(game_id={game.game_id}, reason=no_requested_bookmakers)"
            )
            continue

        for bookmaker in bookmakers:
            odds_row, parse_reason = _parse_bookmaker(event, bookmaker, game.game_id, snapshot_type)
            if not odds_row:
                skipped_inserts.append(
                    f"game_id={game.game_id} sportsbook={bookmaker.get('key', '?')} "
                    f"snapshot={snapshot_type.value} reason={parse_reason}"
                )
                continue
            valid_for_game = True
            existing = _get_existing_snapshot(
                db,
                game_id=game.game_id,
                sportsbook=odds_row.sportsbook,
                snapshot_type=snapshot_type,
            )
            if existing is not None:
                if is_odds_snapshot_fresh(existing):
                    duplicate_fresh_reuse += 1
                    skipped_inserts.append(
                        f"game_id={game.game_id} sportsbook={existing.sportsbook} "
                        f"snapshot={snapshot_type.value} reason=fresh_existing_snapshot_reused"
                    )
                    if game.game_id not in selected_rows:
                        selected_rows[game.game_id] = existing
                    continue

                _refresh_existing_snapshot(existing, odds_row)
                db.flush()
                refreshed_rows.append(
                    f"id={existing.id} game_id={existing.game_id} sportsbook={existing.sportsbook} "
                    f"snapshot={existing.snapshot_type.value}"
                )
                if game.game_id not in selected_rows:
                    selected_rows[game.game_id] = existing
                continue
            db.add(odds_row)
            db.flush()
            stored.append(odds_row)
            inserted_rows.append(
                f"id={odds_row.id} game_id={odds_row.game_id} sportsbook={odds_row.sportsbook} "
                f"snapshot={odds_row.snapshot_type.value}"
            )
            if game.game_id not in selected_rows:
                selected_rows[game.game_id] = odds_row

        if not valid_for_game:
            unmatched_events.append(
                f"{event.get('away_team', '?')} @ {event.get('home_team', '?')} "
                f"(game_id={game.game_id}, reason=no_valid_bookmaker_markets)"
            )

    db.commit()

    reused = 0
    unresolved_matched_games: list[str] = []
    for game_id in matched_game_ids:
        if game_id in selected_rows:
            continue
        latest = get_latest_odds_snapshot(db, game_id=game_id, snapshot_type=snapshot_type)
        if latest is not None and is_odds_snapshot_fresh(latest):
            selected_rows[game_id] = latest
            reused += 1
            skipped_inserts.append(
                f"game_id={game_id} sportsbook={latest.sportsbook} "
                f"snapshot={snapshot_type.value} reason=fallback_reuse_fresh_existing_snapshot"
            )
        else:
            game = db.query(Game).filter(Game.game_id == game_id).first()
            unresolved_matched_games.append(
                f"game_id={game_id} matchup={game.away_team if game else '?'} @ "
                f"{game.home_team if game else '?'} reason=no_fresh_snapshot_selected"
            )

    event_dates = sorted({_event_game_date(event) for event in raw_events})
    for game in _games_on_dates(db, event_dates):
        if game.game_id in matched_game_ids:
            continue
        unmatched_events.append(
            f"{game.away_team} @ {game.home_team} "
            f"(game_id={game.game_id}, reason=no_api_event_for_game_date)"
        )

    print(
        f"[odds] snapshot={snapshot_type.value} matched_events={matched_event_count} "
        f"matched_games={len(matched_game_ids)} unmatched_entries={len(unmatched_events)} "
        f"stored_new={len(stored)} refreshed_existing={len(refreshed_rows)} "
        f"reused_fresh={reused} duplicate_fresh_reuse={duplicate_fresh_reuse} "
        f"returned_rows={len(selected_rows)}"
    )
    if unmatched_events:
        print(f"[odds] unmatched_sample={unmatched_events[:10]}")
    if skipped_inserts:
        print(f"[odds] skipped_inserts_sample={skipped_inserts[:20]}")
    if inserted_rows:
        print(f"[odds] inserted_rows_sample={inserted_rows[:20]}")
    if refreshed_rows:
        print(f"[odds] refreshed_rows_sample={refreshed_rows[:20]}")
    if unresolved_matched_games:
        print(f"[odds] unresolved_matched_games={unresolved_matched_games[:20]}")

    return list(selected_rows.values())


def is_odds_snapshot_fresh(
    odds_row: GameOdds,
    *,
    now: datetime | None = None,
    max_age_minutes: int | None = None,
) -> bool:
    if not odds_row or not odds_row.fetched_at:
        return False

    max_age = max_age_minutes or ODDS_FRESHNESS_MINUTES.get(odds_row.snapshot_type, 60)
    now_utc = now or datetime.now(timezone.utc)
    fetched_at = odds_row.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age_seconds = (now_utc - fetched_at).total_seconds()
    return 0 <= age_seconds <= (max_age * 60)


def get_latest_odds_snapshot(
    db: Session,
    *,
    game_id: int,
    snapshot_type: SnapshotType,
) -> GameOdds | None:
    return (
        db.query(GameOdds)
        .filter(
            GameOdds.game_id == game_id,
            GameOdds.snapshot_type == snapshot_type,
        )
        .order_by(GameOdds.fetched_at.desc(), GameOdds.id.desc())
        .first()
    )


def get_market_home_probability(odds_row: GameOdds | None) -> float | None:
    if odds_row is None or odds_row.away_ml is None or odds_row.home_ml is None:
        return None
    away_raw = ml_to_implied_prob(odds_row.away_ml)
    home_raw = ml_to_implied_prob(odds_row.home_ml)
    _away_prob, home_prob = remove_vig(away_raw, home_raw)
    return float(home_prob)


def _refresh_existing_snapshot(existing: GameOdds, incoming: GameOdds) -> None:
    existing.fetched_at = incoming.fetched_at
    existing.away_ml = incoming.away_ml
    existing.home_ml = incoming.home_ml
    existing.total_line = incoming.total_line
    existing.over_odds = incoming.over_odds
    existing.under_odds = incoming.under_odds
    existing.runline_away = incoming.runline_away
    existing.runline_odds = incoming.runline_odds


def compute_line_movement(
    db: Session,
    game_id: int,
    sportsbook: str = "consensus",
) -> LineMovement | None:
    """
    Compare open vs pregame snapshots for a game and store the movement summary.
    Call this after the pregame snapshot is stored.
    """
    if sportsbook == "consensus":
        open_odds, pregame_odds = _consensus_open_pregame(db, game_id)
        if not open_odds or not pregame_odds:
            return None
        away_move = pregame_odds.away_prob - open_odds.away_prob
        home_move = pregame_odds.home_prob - open_odds.home_prob
        total_move = (
            float(pregame_odds.total_line) - float(open_odds.total_line)
            if pregame_odds.total_line is not None and open_odds.total_line is not None
            else 0.0
        )
        sharp_away = away_move > 0.04
        sharp_home = home_move > 0.04
    else:
        open_odds = _get_existing_snapshot(
            db,
            game_id=game_id,
            sportsbook=sportsbook,
            snapshot_type=SnapshotType.open,
        ) or get_latest_odds_snapshot(db, game_id=game_id, snapshot_type=SnapshotType.open)
        pregame_odds = _get_existing_snapshot(
            db,
            game_id=game_id,
            sportsbook=sportsbook,
            snapshot_type=SnapshotType.pregame,
        ) or get_latest_odds_snapshot(db, game_id=game_id, snapshot_type=SnapshotType.pregame)

        if not open_odds or not pregame_odds:
            return None

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
            "sportsbook",
            "open_away_ml", "open_home_ml", "open_total",
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


def _consensus_open_pregame(
    db: Session,
    game_id: int,
) -> tuple[_ConsensusSnapshot | None, _ConsensusSnapshot | None]:
    open_rows = _snapshot_rows_by_book(db, game_id=game_id, snapshot_type=SnapshotType.open)
    pregame_rows = _snapshot_rows_by_book(db, game_id=game_id, snapshot_type=SnapshotType.pregame)
    common_books = sorted(set(open_rows) & set(pregame_rows))
    if not common_books:
        return None, None
    return (
        _build_consensus_snapshot([open_rows[book] for book in common_books], common_books),
        _build_consensus_snapshot([pregame_rows[book] for book in common_books], common_books),
    )


def _snapshot_rows_by_book(
    db: Session,
    *,
    game_id: int,
    snapshot_type: SnapshotType,
) -> dict[str, GameOdds]:
    rows = (
        db.query(GameOdds)
        .filter(
            GameOdds.game_id == game_id,
            GameOdds.snapshot_type == snapshot_type,
            GameOdds.away_ml.isnot(None),
            GameOdds.home_ml.isnot(None),
        )
        .order_by(GameOdds.fetched_at.desc(), GameOdds.id.desc())
        .all()
    )
    by_book: dict[str, GameOdds] = {}
    for row in rows:
        by_book.setdefault(row.sportsbook, row)
    return by_book


def _build_consensus_snapshot(
    rows: list[GameOdds],
    books: list[str],
) -> _ConsensusSnapshot:
    away_probs: list[float] = []
    home_probs: list[float] = []
    totals: list[float] = []
    for row in rows:
        away_raw = ml_to_implied_prob(row.away_ml)
        home_raw = ml_to_implied_prob(row.home_ml)
        away_prob, home_prob = remove_vig(away_raw, home_raw)
        away_probs.append(float(away_prob))
        home_probs.append(float(home_prob))
        if row.total_line is not None:
            totals.append(float(row.total_line))

    away_prob = sum(away_probs) / len(away_probs)
    home_prob = sum(home_probs) / len(home_probs)
    total_line = round(sum(totals) / len(totals), 1) if totals else None
    return _ConsensusSnapshot(
        away_ml=_prob_to_american(away_prob),
        home_ml=_prob_to_american(home_prob),
        total_line=total_line,
        away_prob=away_prob,
        home_prob=home_prob,
        books=tuple(books),
    )


def _prob_to_american(probability: float) -> int:
    probability = min(max(float(probability), 0.01), 0.99)
    if probability >= 0.5:
        return int(round(-100 * probability / (1 - probability)))
    return int(round(100 * (1 - probability) / probability))


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
    "Athletics (Sacramento)":  "Oakland Athletics",
}

_TEAM_CANONICAL_NAMES: dict[str, str] = {
    "oakland athletics": "athletics",
    "athletics": "athletics",
    "sacramento athletics": "athletics",
    "athletics sacramento": "athletics",
}


def _sanitize_url(url: str) -> str:
    parts = urlsplit(url)
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() == "apikey":
            value = "[redacted]"
        query.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _sanitize_http_error(exc: httpx.HTTPError) -> str:
    request = getattr(exc, "request", None)
    response = getattr(exc, "response", None)
    sanitized_url = _sanitize_url(str(request.url)) if request is not None else None
    status_code = response.status_code if response is not None else None
    if status_code is not None and sanitized_url is not None:
        return f"Odds API request failed with status {status_code} for {sanitized_url}"
    if sanitized_url is not None:
        return f"Odds API request failed for {sanitized_url}"
    return "Odds API request failed"


def _normalize_team_name(value: str | None) -> str:
    if not value:
        return ""
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in value)
    compact = " ".join(normalized.split())
    return _TEAM_CANONICAL_NAMES.get(compact, compact)


def _games_on_dates(db: Session, game_dates: list[date]) -> list[Game]:
    if not game_dates:
        return []
    return db.query(Game).filter(Game.game_date.in_(game_dates)).all()


def _parse_event_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET)


def _event_game_date(event: dict) -> datetime.date:
    event_dt = _parse_event_datetime(event.get("commence_time"))
    if event_dt is not None:
        return event_dt.date()
    return datetime.now(ET).date()


def _sorted_bookmakers(event: dict, requested_books: list[str]) -> list[dict]:
    priority = {book: idx for idx, book in enumerate(requested_books)}
    bookmakers = [
        book for book in event.get("bookmakers", [])
        if not requested_books or book.get("key") in priority
    ]
    return sorted(bookmakers, key=lambda book: priority.get(book.get("key"), len(priority)))


def _get_existing_snapshot(
    db: Session,
    *,
    game_id: int,
    sportsbook: str,
    snapshot_type: SnapshotType,
) -> GameOdds | None:
    return (
        db.query(GameOdds)
        .filter(
            GameOdds.game_id == game_id,
            GameOdds.sportsbook == sportsbook,
            GameOdds.snapshot_type == snapshot_type,
        )
        .order_by(GameOdds.fetched_at.desc(), GameOdds.id.desc())
        .first()
    )


def _match_game(db: Session, event: dict) -> tuple[Game | None, str | None]:
    """Match an odds event to a DB game using exact normalized team aliases."""
    raw_away = event.get("away_team", "")
    raw_home = event.get("home_team", "")
    if not raw_away or not raw_home:
        return None, "missing_team_names"

    away_name = _ODDS_TO_MLB.get(raw_away, raw_away)
    home_name = _ODDS_TO_MLB.get(raw_home, raw_home)
    game_date = _event_game_date(event)
    away_norm = _normalize_team_name(away_name)
    home_norm = _normalize_team_name(home_name)

    games_on_date = db.query(Game).filter(Game.game_date == game_date).all()
    if not games_on_date:
        return None, f"no_games_on_date:{game_date.isoformat()}"

    for game in games_on_date:
        if (
            _normalize_team_name(game.away_team) == away_norm
            and _normalize_team_name(game.home_team) == home_norm
        ):
            return game, None

    team_match_any_date = (
        db.query(Game)
        .filter(
            Game.away_team.ilike(away_name),
            Game.home_team.ilike(home_name),
        )
        .first()
    )
    if team_match_any_date is not None:
        return None, (
            f"team_match_date_mismatch:db_date={team_match_any_date.game_date.isoformat()}"
        )

    return None, "no_exact_team_match_on_date"


def _parse_bookmaker(
    event: dict,
    bookmaker: dict,
    game_id: int,
    snapshot_type: SnapshotType,
) -> tuple[GameOdds | None, str | None]:
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
        return None, "missing_h2h_prices"
    return row, None
