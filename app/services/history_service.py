"""Immutable observations. Times are UTC; availability is never provider backdated."""
from datetime import datetime, timezone
import hashlib
import json
import math

from app.models.schema import ForecastHistory, PriceHistory, DecisionHistory, Game, EdgeResult

POLICY_VERSION = "live-policy-history-v1"


def utc(value):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def dumps(value):
    def normalize(item):
        if isinstance(item, dict):
            return {str(k):normalize(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(v) for v in item]
        if isinstance(item, set):
            return sorted(normalize(v) for v in item)
        if isinstance(item, float) and not math.isfinite(item):
            return {"nonfinite": str(item)}
        return item
    return json.dumps(normalize(value), sort_keys=True, default=str, allow_nan=False)


def append_forecast(db, *, game_id, model_version, calibration_version, inputs, outputs,
                    created_at=None, market=None, sportsbook=None, line=None, price_id=None):
    encoded = dumps(inputs)
    game = db.get(Game, game_id)
    row = ForecastHistory(game_id=game_id, model_version=model_version,
        calibration_version=calibration_version, feature_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        inputs_json=encoded, outputs_json=dumps(outputs), created_at=utc(created_at) or datetime.now(timezone.utc),
        scheduled_start=utc(game.start_time) if game else None, market=market,
        sportsbook=sportsbook, line=line, price_id=price_id)
    db.add(row)
    return row


def append_prices(db, odds, *, bookmaker=None, fetched_at=None):
    now = utc(fetched_at) or datetime.now(timezone.utc)
    game = db.get(Game, odds.game_id)
    markets = {m["key"]: m for m in (bookmaker or {}).get("markets", [])}
    rows = []
    for market, key, first, second, line in (
        ("moneyline", "h2h", odds.home_ml, odds.away_ml, None),
        ("totals", "totals", odds.over_odds, odds.under_odds, odds.total_line),
    ):
        if first is None or second is None or first == 0 or second == 0:
            continue
        if market == "totals" and line is None:
            continue
        totals = markets.get("totals", {})
        points = [o.get("point") for o in totals.get("outcomes", []) if o.get("name") in {"Over", "Under"}]
        if market == "totals" and points and (len(points) != 2 or None in points or points[0] != points[1]):
            continue  # Never invent a same-line two-sided quote from mismatched provider sides.
        source = markets.get(key, {}).get("last_update") or (bookmaker or {}).get("last_update")
        row = PriceHistory(game_id=odds.game_id, sportsbook=odds.sportsbook,
            market=market, line=line, first_price=first, second_price=second,
            fetched_at=now, source_updated_at=utc(source),
            scheduled_start=utc(game.start_time) if game else None)
        db.add(row)
        rows.append(row)
    return rows


def forecast_as_of(db, game_id, at, *, model_version=None, market=None):
    query = db.query(ForecastHistory).filter(ForecastHistory.game_id == game_id,
                                           ForecastHistory.created_at <= utc(at))
    if model_version is not None:
        query = query.filter(ForecastHistory.model_version == model_version)
    if market is not None:
        query = query.filter(ForecastHistory.market == market)
    return query.order_by(ForecastHistory.created_at.desc(), ForecastHistory.id.desc()).first()


def price_as_of(db, game_id, at, *, sportsbook, market, line=None, strict=False):
    query = db.query(PriceHistory).filter(PriceHistory.game_id == game_id,
        PriceHistory.sportsbook == sportsbook, PriceHistory.market == market)
    query = query.filter(PriceHistory.fetched_at < utc(at) if strict else PriceHistory.fetched_at <= utc(at))
    if market == "totals":
        query = query.filter(PriceHistory.line == line)
    return query.order_by(PriceHistory.fetched_at.desc(), PriceHistory.id.desc()).first()


def append_decision(db, row, *, created_at=None):
    row = dict(row)
    from app.services.betting_policy import BETTING_PROFILES
    row["betting_policy_config"] = BETTING_PROFILES
    policy_hash = hashlib.sha256(dumps(BETTING_PROFILES).encode()).hexdigest()[:16]
    game = db.get(Game, row["game_id"])
    now = utc(created_at) or datetime.now(timezone.utc)
    entry_at = utc(row.get("entry_time")) or now
    if entry_at > now:
        entry_at = now
    forecast = db.query(ForecastHistory).filter(ForecastHistory.game_id == row["game_id"],
        ForecastHistory.market.is_(None), ForecastHistory.created_at <= entry_at).order_by(
        ForecastHistory.created_at.desc(), ForecastHistory.id.desc()).first()
    edge = db.get(EdgeResult, row["edge_result_id"]) if row.get("edge_result_id") else None
    if edge:
        row["frozen_edge"] = {c.name:getattr(edge, c.name) for c in EdgeResult.__table__.columns}
        row.setdefault("sportsbook", edge.sportsbook)
        if row.get("line") is None and edge.book_total is not None:
            row["line"] = float(edge.book_total)
    play = row.get("play") or row.get("recommended_play")
    market = "totals" if play in {"over", "under"} else "moneyline" if play in {"home_ml", "away_ml"} else None
    price = price_as_of(db, row["game_id"], entry_at, sportsbook=row.get("sportsbook"),
                        market=market, line=row.get("line")) if market else None
    if edge and market:
        first, second = (edge.home_ml, edge.away_ml) if market == "moneyline" else (edge.over_odds, edge.under_odds)
        query = db.query(PriceHistory).filter(PriceHistory.game_id == row["game_id"],
            PriceHistory.market == market, PriceHistory.sportsbook == row.get("sportsbook"),
            PriceHistory.first_price == first, PriceHistory.second_price == second, PriceHistory.fetched_at <= entry_at)
        if market == "totals":
            query = query.filter(PriceHistory.line == row.get("line"))
        price = query.order_by(PriceHistory.fetched_at.desc(), PriceHistory.id.desc()).first()
    record = DecisionHistory(game_id=row["game_id"], created_at=now,
        scheduled_start=utc(game.start_time) if game else None,
        policy_version=f"{POLICY_VERSION}:{policy_hash}", decision_status=row.get("decision_status") or "BLOCKED",
        reason=row.get("decision_reason") or row.get("reason") or "unspecified",
        play=play, market=market, forecast_id=forecast.id if forecast else None,
        price_id=price.id if price else None, payload_json=dumps(row))
    db.add(record)
    return record
