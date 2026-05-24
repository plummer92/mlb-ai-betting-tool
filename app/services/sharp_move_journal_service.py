from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.schema import EdgeResult, Game, GameOdds, LineMovement, SharpMoveJournal, SnapshotType
from app.services.ev_math import implied_prob_raw

ET = ZoneInfo("America/New_York")
PLAYS = ("away_ml", "home_ml", "over", "under")


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _latest_rows_by_game(db: Session, game_ids: list[int], snapshot_type: SnapshotType) -> dict[int, GameOdds]:
    if not game_ids:
        return {}
    latest: dict[int, GameOdds] = {}
    rows = (
        db.query(GameOdds)
        .filter(GameOdds.game_id.in_(game_ids), GameOdds.snapshot_type == snapshot_type)
        .order_by(GameOdds.game_id.asc(), GameOdds.fetched_at.desc(), GameOdds.id.desc())
        .all()
    )
    for row in rows:
        latest.setdefault(row.game_id, row)
    return latest


def _latest_any_odds_by_game(db: Session, game_ids: list[int]) -> dict[int, GameOdds]:
    if not game_ids:
        return {}
    latest: dict[int, GameOdds] = {}
    rows = (
        db.query(GameOdds)
        .filter(GameOdds.game_id.in_(game_ids))
        .order_by(GameOdds.game_id.asc(), GameOdds.fetched_at.desc(), GameOdds.id.desc())
        .all()
    )
    for row in rows:
        latest.setdefault(row.game_id, row)
    return latest


def _latest_by_game(rows) -> dict[int, object]:
    latest: dict[int, object] = {}
    for row in rows:
        latest.setdefault(row.game_id, row)
    return latest


def _play_odds_and_line(odds: GameOdds | None, play: str) -> tuple[int | None, float | None]:
    if odds is None:
        return None, None
    if play == "away_ml":
        return odds.away_ml, None
    if play == "home_ml":
        return odds.home_ml, None
    if play == "over":
        return odds.over_odds, float(odds.total_line) if odds.total_line is not None else None
    if play == "under":
        return odds.under_odds, float(odds.total_line) if odds.total_line is not None else None
    return None, None


def _edge_pct_for_play(edge: EdgeResult | None, play: str) -> float | None:
    if edge is None:
        return None
    if play == "away_ml":
        return float(edge.edge_away) if edge.edge_away is not None else None
    if play == "home_ml":
        return float(edge.edge_home) if edge.edge_home is not None else None
    if play == "over":
        return float(edge.total_edge or 0) / 100 if edge.total_edge is not None else None
    if play == "under":
        return -float(edge.total_edge or 0) / 100 if edge.total_edge is not None else None
    return None


def _ev_for_play(edge: EdgeResult | None, play: str) -> float | None:
    if edge is None:
        return None
    if play == "away_ml":
        return float(edge.ev_away) if edge.ev_away is not None else None
    if play == "home_ml":
        return float(edge.ev_home) if edge.ev_home is not None else None
    if play == "over":
        return float(edge.ev_over) if edge.ev_over is not None else None
    if play == "under":
        return float(edge.ev_under) if edge.ev_under is not None else None
    return None


def _line_move_for_play(open_line: float | None, pregame_line: float | None, play: str) -> float | None:
    if open_line is None or pregame_line is None:
        return None
    if play == "over":
        return round(pregame_line - open_line, 3)
    if play == "under":
        return round(open_line - pregame_line, 3)
    return None


def _price_move(open_price: int | None, pregame_price: int | None) -> float | None:
    if open_price is None or pregame_price is None:
        return None
    return round(implied_prob_raw(pregame_price) - implied_prob_raw(open_price), 4)


def _movement_bucket(line_move: float | None, price_move: float | None, play: str) -> str:
    if play in {"over", "under"}:
        magnitude = abs(line_move or 0)
        if magnitude >= 0.5:
            return "total_steam"
        if magnitude >= 0.2:
            return "minor_move"
        return "flat" if line_move is not None else "no_movement"
    magnitude = abs(price_move or 0)
    if magnitude >= 0.04:
        return "ml_steam"
    if magnitude >= 0.02:
        return "minor_move"
    return "flat" if price_move is not None else "no_movement"


def _market_signal(play: str, line_move: float | None, price_move: float | None, bucket: str) -> str:
    if bucket == "no_movement":
        return "NO_MOVE"
    if bucket == "flat":
        return "QUIET_MARKET"
    if play == "over" and (line_move or 0) > 0:
        return "TOTAL_STEAM_OVER"
    if play == "under" and (line_move or 0) > 0:
        return "TOTAL_STEAM_UNDER"
    if play == "home_ml" and (price_move or 0) > 0:
        return "ML_STEAM_HOME"
    if play == "away_ml" and (price_move or 0) > 0:
        return "ML_STEAM_AWAY"
    if bucket in {"ml_steam", "total_steam", "minor_move"}:
        return "MOVE_AGAINST_PLAY"
    return "QUIET_MARKET"


def _snapshot_row(
    *,
    game: Game,
    play: str,
    open_odds: GameOdds | None,
    pregame_odds: GameOdds | None,
    latest_odds: GameOdds | None,
    movement: LineMovement | None,
    edge: EdgeResult | None,
) -> dict:
    open_price, open_line = _play_odds_and_line(open_odds, play)
    pregame_price, pregame_line = _play_odds_and_line(pregame_odds, play)
    latest_price, latest_line = _play_odds_and_line(latest_odds, play)
    line_move = _line_move_for_play(open_line, pregame_line, play)
    price_move = _price_move(open_price, pregame_price)
    bucket = _movement_bucket(line_move, price_move, play)
    model_play = edge.recommended_play if edge else None
    row = {
        "game_id": game.game_id,
        "edge_result_id": edge.id if edge else None,
        "game_date": game.game_date.isoformat() if game.game_date else datetime.now(ET).date().isoformat(),
        "matchup": f"{game.away_team} @ {game.home_team}",
        "play": play,
        "open_fetched_at": _iso(open_odds.fetched_at) if open_odds else None,
        "move_observed_at": _iso(pregame_odds.fetched_at) if pregame_odds else None,
        "latest_fetched_at": _iso(latest_odds.fetched_at) if latest_odds else None,
        "sportsbook": (pregame_odds.sportsbook if pregame_odds else None) or (open_odds.sportsbook if open_odds else None),
        "open_line": open_line,
        "open_price": open_price,
        "pregame_line": pregame_line,
        "pregame_price": pregame_price,
        "latest_line": latest_line,
        "latest_price": latest_price,
        "line_move": line_move,
        "price_move": price_move,
        "line_clv": line_move,
        "price_clv": price_move,
        "movement_bucket": bucket,
        "market_signal": _market_signal(play, line_move, price_move, bucket),
        "model_recommended_play": model_play,
        "model_agreed": model_play == play,
        "model_edge_pct": _edge_pct_for_play(edge, play),
        "model_ev": _ev_for_play(edge, play),
        "readiness": (
            "CLV_READY" if pregame_odds and movement else
            "PREGAME_SNAPSHOT_MISSING" if not pregame_odds else
            "MOVEMENT_MISSING"
        ),
    }
    return row


def _as_date(value: str | date | None) -> date:
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(value[:10])
    return datetime.now(ET).date()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _find_existing(db: Session, row: dict) -> SharpMoveJournal | None:
    return (
        db.query(SharpMoveJournal)
        .filter(
            SharpMoveJournal.game_date == _as_date(row.get("game_date")),
            SharpMoveJournal.game_id == row["game_id"],
            SharpMoveJournal.play == row["play"],
        )
        .one_or_none()
    )


def _apply_row(snapshot: SharpMoveJournal, row: dict) -> None:
    snapshot.game_id = row["game_id"]
    snapshot.edge_result_id = row.get("edge_result_id")
    snapshot.game_date = _as_date(row.get("game_date"))
    snapshot.matchup = row.get("matchup")
    snapshot.play = row["play"]
    snapshot.open_fetched_at = _parse_dt(row.get("open_fetched_at"))
    snapshot.move_observed_at = _parse_dt(row.get("move_observed_at"))
    snapshot.latest_fetched_at = _parse_dt(row.get("latest_fetched_at"))
    snapshot.sportsbook = row.get("sportsbook")
    snapshot.open_line = _decimal(row.get("open_line"))
    snapshot.open_price = row.get("open_price")
    snapshot.pregame_line = _decimal(row.get("pregame_line"))
    snapshot.pregame_price = row.get("pregame_price")
    snapshot.latest_line = _decimal(row.get("latest_line"))
    snapshot.latest_price = row.get("latest_price")
    snapshot.line_move = _decimal(row.get("line_move"))
    snapshot.price_move = _decimal(row.get("price_move"))
    snapshot.line_clv = _decimal(row.get("line_clv"))
    snapshot.price_clv = _decimal(row.get("price_clv"))
    snapshot.movement_bucket = row.get("movement_bucket") or "no_movement"
    snapshot.market_signal = row.get("market_signal") or "NO_MOVE"
    snapshot.model_recommended_play = row.get("model_recommended_play")
    snapshot.model_agreed = bool(row.get("model_agreed"))
    snapshot.model_edge_pct = _decimal(row.get("model_edge_pct"))
    snapshot.model_ev = _decimal(row.get("model_ev"))
    snapshot.readiness = row.get("readiness")
    snapshot.snapshot_json = json.dumps(row, sort_keys=True)


def build_sharp_move_rows(db: Session, target_date: date | None = None) -> list[dict]:
    target_date = target_date or datetime.now(ET).date()
    games = db.query(Game).filter(Game.game_date == target_date).order_by(Game.start_time.asc(), Game.game_id.asc()).all()
    game_ids = [game.game_id for game in games]
    open_by_game = _latest_rows_by_game(db, game_ids, SnapshotType.open)
    pregame_by_game = _latest_rows_by_game(db, game_ids, SnapshotType.pregame)
    latest_by_game = _latest_any_odds_by_game(db, game_ids)
    movement_by_game = _latest_by_game(
        db.query(LineMovement)
        .filter(LineMovement.game_id.in_(game_ids))
        .order_by(LineMovement.game_id.asc(), LineMovement.calculated_at.desc(), LineMovement.id.desc())
        .all()
    ) if game_ids else {}
    edge_by_game = _latest_by_game(
        db.query(EdgeResult)
        .filter(EdgeResult.game_id.in_(game_ids), EdgeResult.is_active.is_(True))
        .order_by(EdgeResult.game_id.asc(), EdgeResult.calculated_at.desc(), EdgeResult.id.desc())
        .all()
    ) if game_ids else {}

    rows = []
    for game in games:
        for play in PLAYS:
            rows.append(
                _snapshot_row(
                    game=game,
                    play=play,
                    open_odds=open_by_game.get(game.game_id),
                    pregame_odds=pregame_by_game.get(game.game_id),
                    latest_odds=latest_by_game.get(game.game_id),
                    movement=movement_by_game.get(game.game_id),
                    edge=edge_by_game.get(game.game_id),
                )
            )
    rows.sort(key=lambda row: (row["matchup"], row["play"]))
    return rows


def persist_sharp_move_journal(db: Session, target_date: date | None = None) -> dict:
    rows = build_sharp_move_rows(db, target_date=target_date)
    created = 0
    updated = 0
    for row in rows:
        snapshot = _find_existing(db, row)
        if snapshot is None:
            snapshot = SharpMoveJournal(
                game_id=row["game_id"],
                game_date=_as_date(row.get("game_date")),
                play=row["play"],
                movement_bucket=row.get("movement_bucket") or "no_movement",
                market_signal=row.get("market_signal") or "NO_MOVE",
            )
            db.add(snapshot)
            created += 1
        else:
            updated += 1
        _apply_row(snapshot, row)
    db.commit()
    return {
        "status": "ok",
        "created": created,
        "updated": updated,
        "total": len(rows),
        "by_signal": dict(Counter(row["market_signal"] for row in rows)),
        "by_bucket": dict(Counter(row["movement_bucket"] for row in rows)),
        "model_agreed_moves": sum(
            1 for row in rows
            if row["model_agreed"] and row["market_signal"] not in {"NO_MOVE", "QUIET_MARKET"}
        ),
        "rows": rows,
    }
