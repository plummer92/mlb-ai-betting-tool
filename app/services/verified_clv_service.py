"""Ex-post CLV from immutable, two-sided same-book/same-line quotes.

This module is reporting only: it is never imported by decision-time scoring.
Missing observations remain None, including while a game is still pregame.
"""
from collections import defaultdict
from datetime import datetime, timezone
import json

import numpy as np

from app.models.schema import PriceHistory, ForecastHistory, DecisionHistory, PaperTrade, BetAlert, EdgeResult
from app.services.ev_math import implied_prob_raw, american_to_decimal
from app.services.history_service import utc, price_as_of
from app.services.shadow_math import JOINT_VERSION


def no_vig(first, second):
    a, b = implied_prob_raw(first), implied_prob_raw(second)
    return a / (a + b)


def verified_clv(db, *, entry, play, decided_at, scheduled_start, as_of=None):
    now = utc(as_of) or datetime.now(timezone.utc)
    start, decision = utc(scheduled_start), utc(decided_at)
    empty = dict(verification="unavailable", reason="missing_lineage", probability_clv=None,
                 price_clv=None, decimal_price_clv=None, close_id=None)
    if not entry or not start or not decision:
        return empty
    if utc(entry.scheduled_start) != start or entry.market != ("totals" if play in {"over", "under"} else "moneyline"):
        return {**empty, "reason": "market_or_start_lineage_mismatch"}
    if utc(entry.fetched_at) > decision or decision >= start:
        return {**empty, "reason": "entry_not_available_pregame"}
    if now < start:
        return {**empty, "verification": "pending", "reason": "first_pitch_not_reached"}
    close = price_as_of(db, entry.game_id, start, sportsbook=entry.sportsbook,
                        market=entry.market, line=entry.line, strict=True)
    if not close or utc(close.fetched_at) <= decision:
        return {**empty, "reason": "no_post_entry_close"}
    first = play in {"home_ml", "over"}
    if play not in {"home_ml", "away_ml", "over", "under"}:
        return {**empty, "reason": "unsupported_play"}
    ep, cp = (entry.first_price, close.first_price) if first else (entry.second_price, close.second_price)
    entry_p, close_p = no_vig(entry.first_price, entry.second_price), no_vig(close.first_price, close.second_price)
    if not first:
        entry_p, close_p = 1-entry_p, 1-close_p
    consensus = entry.sportsbook in {"consensus", "betstack_consensus"}
    return dict(verification="fallback_consensus" if consensus else "verified_same_book",
        reason="synthetic_consensus_is_not_a_sportsbook" if consensus else None, probability_clv=close_p-entry_p,
        price_clv=implied_prob_raw(cp)-implied_prob_raw(ep),
        decimal_price_clv=american_to_decimal(ep)/american_to_decimal(cp)-1,
        close_id=close.id, entry_id=entry.id, closing_sportsbook=close.sportsbook,
        closing_odds=cp, closing_line=close.line,
        close_at=utc(close.fetched_at).isoformat(), price_age_minutes=(start-utc(close.fetched_at)).total_seconds()/60)


def clustered_interval(rows, key="probability_clv", *, confidence=.90, seed=20261009):
    groups = defaultdict(list)
    for row in rows:
        if row.get(key) is not None:
            groups[row["date"]].append(float(row[key]))
    if not groups:
        return None, None, None
    values = list(groups.values())
    mean = float(np.mean([x for group in values for x in group]))
    if len(values) < 2:
        return mean, None, None
    sums = np.array([sum(v) for v in values])
    counts = np.array([len(v) for v in values])
    sample = np.random.default_rng(seed).integers(0, len(values), (3000, len(values)))
    means = sums[sample].sum(axis=1) / counts[sample].sum(axis=1)
    tail = (1-confidence)/2
    lo, hi = np.quantile(means, [tail, 1-tail])
    return mean, float(lo), float(hi)


def summarize(rows):
    verified = [r for r in rows if r["verification"] == "verified_same_book"]
    mean, lo, hi = clustered_interval(verified)
    price, price_lo, price_hi = clustered_interval(verified, "price_clv")
    return dict(observations=len(rows), verified=len(verified), coverage=len(verified)/len(rows) if rows else 0.,
        pending=sum(r["verification"] == "pending" for r in rows),
        fallback=sum(r["verification"].startswith("fallback") for r in rows),
        mean_probability_clv=mean, low90=lo, high90=hi, mean_price_clv=price,
        price_low90=price_lo, price_high90=price_hi,
        interval_method="date-cluster bootstrap, 3000 resamples; null with fewer than two dates")


def get_verified_clv_report(db, *, as_of=None):
    rows = []
    now = utc(as_of) or datetime.now(timezone.utc)
    # Shadow picks freeze the chosen side at prediction time. Every rejected decision is retained too.
    for kind, model in (("shadow", ForecastHistory), ("decision", DecisionHistory)):
        query = db.query(model).filter(model.created_at <= now)
        if kind == "shadow":
            query = query.filter(model.market.isnot(None))
        for record in query.all():
            payload = json.loads(record.outputs_json if kind == "shadow" else record.payload_json)
            play = payload.get("play") if kind == "shadow" else record.play
            entry = db.get(PriceHistory, record.price_id)
            rows.append(dict(kind=kind, id=record.id, market=record.market, game_id=record.game_id,
                model_version=record.model_version if kind == "shadow" else None,
                date=utc(record.scheduled_start).date().isoformat() if record.scheduled_start else "unknown",
                **verified_clv(db, entry=entry, play=play, decided_at=record.created_at,
                               scheduled_start=record.scheduled_start, as_of=now)))
    paper_entries = {}
    for record in db.query(DecisionHistory).filter(DecisionHistory.created_at <= now).all():
        payload = json.loads(record.payload_json)
        if payload.get("action") == "paper_placement" and payload.get("paper_trade_id"):
            paper_entries.setdefault(payload["paper_trade_id"], (record, payload))
    for trade in db.query(PaperTrade).filter(PaperTrade.placed_at <= now).all():
        alert = db.get(BetAlert, trade.bet_alert_id) if trade.bet_alert_id else None
        edge = db.get(EdgeResult, trade.edge_result_id) if trade.edge_result_id else None
        decision = utc(alert.alert_time) if alert else utc(trade.placed_at)
        market = "totals" if trade.play in {"over", "under"} else "moneyline"
        # Legacy mutable edge terms cannot establish a verified entry. Match a frozen quote exactly.
        frozen = paper_entries.get(trade.id)
        entry = db.get(PriceHistory, frozen[0].price_id) if frozen and frozen[0].price_id else None
        if frozen and frozen[1].get("entry_time"):
            decision = utc(frozen[1]["entry_time"])
        ep = (entry.first_price if trade.play in {"home_ml", "over"} else entry.second_price) if entry else None
        if trade.odds is None or ep != trade.odds:
            entry = None
        rows.append(dict(kind="paper", id=trade.id, market=market, game_id=trade.game_id,
            date=trade.game_date.isoformat(), **verified_clv(db, entry=entry, play=trade.play,
            decided_at=decision, scheduled_start=entry.scheduled_start if entry else None, as_of=now)))
    # Repeated evaluations are shown by kind; never pooled as independent picks in the primary mean.
    unique_shadow = {}
    for row in rows:
        if row["kind"] == "shadow":
            unique_shadow.setdefault((row["model_version"], row["game_id"], row["market"]), row)
    shadow = list(unique_shadow.values())
    candidate_groups = sorted({(r["model_version"], r["market"]) for r in shadow})
    return dict(summary=summarize([r for r in shadow if r["model_version"] == JOINT_VERSION]),
        primary_candidate=JOINT_VERSION, selection="first pregame forecast per game, candidate and market",
        by_candidate=[dict(model_version=v, market=m, **summarize([r for r in shadow if r["model_version"] == v and r["market"] == m]))
                      for v, m in candidate_groups],
        by_kind=[dict(kind=k, **summarize([r for r in rows if r["kind"] == k])) for k in ("shadow", "decision", "paper")],
        by_market=[dict(market=m, kind=k, **summarize([r for r in (shadow if k == "shadow" else rows) if r["market"] == m and r["kind"] == k]))
                   for k in ("shadow", "decision", "paper") for m in ("moneyline", "totals")], recent=rows[-50:])
