from datetime import timedelta
import pytest

from test_improvement_history import db, NOW
from app.models.schema import PriceHistory, Game, PaperTrade, EdgeResult
from app.services.history_service import utc
from app.services.verified_clv_service import verified_clv, clustered_interval, get_verified_clv_report
from app.services.market_audit_service import _closing_odds_for_trade


def quote(db, *, time, book="a", market="moneyline", line=None, first=-110, second=100):
    row = PriceHistory(game_id=1, sportsbook=book, market=market, line=line, first_price=first,
        second_price=second, fetched_at=time, scheduled_start=NOW+timedelta(hours=2))
    db.add(row)
    db.commit()
    return row


def test_first_pitch_strict_cutoff_and_no_future_decision_use(db):
    entry = quote(db, time=NOW)
    close = quote(db, time=NOW+timedelta(minutes=90), first=-150, second=130)
    quote(db, time=NOW+timedelta(hours=2), first=-300, second=250)
    args = dict(entry=entry, play="home_ml", decided_at=NOW, scheduled_start=entry.scheduled_start)
    pending = verified_clv(db, **args, as_of=NOW+timedelta(minutes=100))
    assert pending["verification"] == "pending" and pending["probability_clv"] is None
    result = verified_clv(db, **args, as_of=NOW+timedelta(hours=3))
    assert result["close_id"] == close.id
    assert result["probability_clv"] == pytest.approx(.6/(.6+1/2.3) - (110/210)/(110/210+.5))
    assert result["price_clv"] > 0 and result["decimal_price_clv"] > 0
    away = verified_clv(db, **{**args, "play": "away_ml"}, as_of=NOW+timedelta(hours=3))
    assert away["probability_clv"] == pytest.approx(-result["probability_clv"])


def test_other_book_moved_line_self_quote_and_future_entry_never_verified(db):
    entry = quote(db, time=NOW, market="totals", line=8)
    quote(db, time=NOW+timedelta(minutes=90), market="totals", line=8.5, first=-150)
    quote(db, time=NOW+timedelta(minutes=90), book="b", market="totals", line=8, first=-150)
    args = dict(entry=entry, play="over", decided_at=NOW, scheduled_start=entry.scheduled_start,
                as_of=NOW+timedelta(hours=3))
    assert verified_clv(db, **args)["reason"] == "no_post_entry_close"
    close = quote(db, time=NOW+timedelta(minutes=100), market="totals", line=8, first=-150)
    assert verified_clv(db, **args)["close_id"] == close.id
    assert verified_clv(db, **{**args, "decided_at": NOW-timedelta(minutes=1)})["probability_clv"] is None
    assert verified_clv(db, **{**args, "scheduled_start": None})["probability_clv"] is None


def test_missing_is_not_zero_and_intervals_cluster_dates():
    assert clustered_interval([]) == (None, None, None)
    assert clustered_interval([{"date":"one", "probability_clv":.1}]) == (.1, None, None)
    result = clustered_interval([{"date":str(i), "probability_clv":.1} for i in range(3)])
    assert result == pytest.approx((.1, .1, .1))


def test_empty_report_and_legacy_missing_start_are_unverified(db):
    report = get_verified_clv_report(db, as_of=NOW)
    assert report["summary"]["mean_probability_clv"] is None
    db.get(Game, 1).start_time = None
    db.commit()
    assert _closing_odds_for_trade(db, PaperTrade(game_id=1), EdgeResult(sportsbook="a")) is None


def test_consensus_quotes_are_fallback_never_verified(db):
    from app.services.verified_clv_service import summarize
    entry = quote(db, time=NOW, book="betstack_consensus")
    quote(db, time=NOW+timedelta(minutes=90), book="betstack_consensus", first=-150, second=130)
    result = verified_clv(db, entry=entry, play="home_ml", decided_at=NOW,
                          scheduled_start=entry.scheduled_start, as_of=NOW+timedelta(hours=3))
    assert result["verification"] == "fallback_consensus" and result["probability_clv"] > 0
    summary = summarize([{**result, "date":"2026-10-09"}])
    assert summary["fallback"] == 1 and summary["verified"] == 0 and summary["mean_probability_clv"] is None


def test_dashboard_health_reads_cached_verified_clv_without_scanning(monkeypatch):
    from app.routes import dashboard
    from app.services import report_cache, verified_clv_service

    def boom(*args, **kwargs):
        raise AssertionError("dashboard health must not run the full verified-CLV scan")

    monkeypatch.setattr(verified_clv_service, "get_verified_clv_report", boom)
    report_cache.clear_report_cache()
    assert report_cache.peek_cached(dashboard.VERIFIED_CLV_CACHE_KEY) is None
    report_cache.set_cached(dashboard.VERIFIED_CLV_CACHE_KEY, 60, {"status": "ok", "marker": 1})
    assert report_cache.peek_cached(dashboard.VERIFIED_CLV_CACHE_KEY)["marker"] == 1
    report_cache.clear_report_cache()
