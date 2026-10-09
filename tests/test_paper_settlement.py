from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.betting import BankrollSnapshot, BetExecution, BetOrder
from app.models.schema import BetAlert, EdgeResult, Game, GameOdds, GameOutcomeReview, PaperTrade, Prediction
from app.services.paper_settlement_service import backfill_finished_paper_bets, final_scores
from app.services.review_service import resolve_completed_games


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def seed(db, *, status="Final", away=4, home=5, play="over", line=8.5, odds=120):
    game = Game(game_id=1, game_date=date(2026, 3, 27), season=2026,
                away_team="Away", home_team="Home", status=status,
                final_away_score=away, final_home_score=home)
    alert = BetAlert(id=1, game_id=1, prediction_id=1, edge_result_id=1, game_date=game.game_date,
                     play=play, synopsis="paper", status="sent", edge_pct=.1,
                     ev=.1, confidence="strong")
    trade = PaperTrade(id=1, bet_alert_id=1, game_id=1, prediction_id=1,
                       game_date=game.game_date, play=play, paper_stake=100,
                       odds=odds, line=line, status="open")
    order = BetOrder(id=1, game_id=1, sportsbook="paper", provider_mode="paper",
                     market_type="total", side=play, requested_line=line,
                     requested_odds=-110, requested_stake=20, status="placed_paper")
    fill = BetExecution(bet_order_id=1, placed_odds=odds, placed_line=line,
                        placed_stake=10, fill_status="filled")
    db.add_all([game, alert, trade, order, fill, BankrollSnapshot(
        provider_mode="paper", sportsbook="paper", bankroll=90, available_balance=90,
        captured_at=datetime.now(timezone.utc))])
    db.commit()
    return game, alert, trade, order, fill


@pytest.mark.parametrize("play,line,odds,result,profit", [
    ("over", 8.5, 120, "win", 120), ("under", 8.5, -105, "loss", -100),
    ("over", 9, -110, "push", 0), ("under", 9, -110, "push", 0),
    ("home_ml", None, -125, "win", 80), ("away_ml", None, 150, "loss", -100),
])
def test_historical_both_ledgers_use_taken_price_and_are_idempotent(db, play, line, odds, result, profit):
    _, alert, trade, order, fill = seed(db, play=play, line=line, odds=odds)
    with patch("app.services.execution_service.get_provider", side_effect=AssertionError("provider used")):
        first = backfill_finished_paper_bets(db)
        second = backfill_finished_paper_bets(db)
    assert first["paper_trades"]["settled"] == first["bet_orders"]["settled"] == 1
    assert second["paper_trades"]["settled"] == second["bet_orders"]["settled"] == 0
    assert trade.result == fill.settled_result == result
    assert trade.profit_loss == Decimal(profit)
    assert float(fill.profit_loss_dollars) == pytest.approx(profit / 10)
    assert order.status == trade.status == "settled"
    assert alert.bet_result is None  # Does not rely on or rewrite alert grading.
    assert db.query(BankrollSnapshot).count() == 2
    balance = db.query(BankrollSnapshot).order_by(BankrollSnapshot.id.desc()).first()
    assert float(balance.available_balance) == pytest.approx(90 + (10 + profit / 10 if result != "loss" else 0))


@pytest.mark.parametrize("status,away,home", [
    ("Live", 4, 5), ("Preview", 4, 5), ("Cancelled", 4, 5),
    ("Postponed", 4, 5), ("Final", None, 5), ("Final", 0, 0), ("Final", -1, 5),
])
def test_unfinished_or_invalid_scores_never_settle(db, status, away, home):
    _, _, trade, order, _ = seed(db, status=status, away=away, home=home)
    result = backfill_finished_paper_bets(db)
    assert result["paper_trades"]["settled"] == result["bet_orders"]["settled"] == 0
    assert trade.status == "open" and order.status == "placed_paper"
    assert db.query(BankrollSnapshot).count() == 1


def test_execution_uses_filled_line_and_stake(db):
    _, _, trade, order, fill = seed(db, play="over", line=8.5, odds=120)
    fill.placed_line = 9.5
    db.commit()
    backfill_finished_paper_bets(db)
    assert trade.result == "win" and fill.settled_result == "loss"
    assert fill.profit_loss_dollars == -10


@pytest.mark.parametrize("field", ["line", "odds", "stake"])
def test_missing_or_invalid_terms_are_not_fabricated(db, field):
    _, _, trade, order, fill = seed(db)
    if field == "line":
        trade.line = fill.placed_line = order.requested_line = None
    elif field == "odds":
        trade.odds = fill.placed_odds = order.requested_odds = None
    else:
        trade.paper_stake = fill.placed_stake = order.requested_stake = 0
    db.commit()
    result = backfill_finished_paper_bets(db)
    assert result["paper_trades"]["settled"] == (1 if field == "odds" else 0)
    assert result["bet_orders"]["settled"] == 0


def add_review(db, away, home, prediction_id=1):
    db.add(Prediction(prediction_id=prediction_id, game_id=1, model_version="test",
                      away_win_pct=.5, home_win_pct=.5, projected_away_score=4,
                      projected_home_score=5, projected_total=9, confidence_score=0))
    db.add(GameOutcomeReview(game_id=1, prediction_id=prediction_id,
                            game_date=date(2026, 3, 27), actual_outcome_summary="final",
                            final_away_score=away, final_home_score=home,
                            winning_side="home", bet_result="no_bet"))
    db.commit()


def test_consistent_review_can_supply_missing_final_score(db):
    game, _, _, _, _ = seed(db, away=None)
    add_review(db, 4, 5)
    assert final_scores(db, game) == (4, 5)
    assert backfill_finished_paper_bets(db)["paper_trades"]["settled"] == 1


def test_conflicting_review_scores_require_operator_review(db):
    game, _, _, _, _ = seed(db)
    add_review(db, 3, 5)
    assert final_scores(db, game) is None
    add_review(db, 4, 5, prediction_id=2)
    assert final_scores(db, game) is None


def test_live_order_and_already_settled_trade_are_preserved(db):
    _, _, trade, order, _ = seed(db)
    order.provider_mode = "live"
    trade.status = "settled"
    trade.result = "loss"
    trade.profit_loss = -100
    db.commit()
    backfill_finished_paper_bets(db)
    assert trade.profit_loss == -100 and order.status == "placed_paper"


def test_scheduled_review_settles_old_ledgers_without_recent_predictions(db):
    seed(db)
    with patch("app.services.review_service.fetch_schedule_for_date", side_effect=AssertionError("fetch")):
        result = resolve_completed_games(db)
    assert result["resolved"] == 0
    assert result["paper_settlement"]["paper_trades"]["settled"] == 1
    assert result["paper_settlement"]["bet_orders"]["settled"] == 1


def test_bankroll_commit_failure_rolls_back_order_settlement(db):
    from app.services.execution_service import settle_paper_bets
    _, _, _, order, fill = seed(db)
    with patch.object(db, "commit", side_effect=RuntimeError("commit failed")):
        with pytest.raises(RuntimeError):
            settle_paper_bets(db)
    db.rollback()
    assert order.status == "placed_paper" and fill.settled_result is None
    assert db.query(BankrollSnapshot).count() == 1


@pytest.mark.parametrize("future,wrong_line", [(False, False), (True, False), (False, True)])
def test_legacy_missing_prices_recover_only_matching_asof_snapshot(db, future, wrong_line):
    from app.services.paper_trade_service import get_paper_summary
    _, alert, trade, _, _ = seed(db)
    trade.odds = None
    alert.alert_time = datetime(2026, 3, 27, 16, tzinfo=timezone.utc)
    # Mirror was created months later; its timestamp must not admit future odds.
    trade.placed_at = datetime(2026, 5, 16, tzinfo=timezone.utc)
    odds = GameOdds(id=1, game_id=1, sportsbook="test", snapshot_type="open",
                    fetched_at=alert.alert_time + timedelta(hours=1 if future else -1),
                    away_ml=120, home_ml=-130, total_line=9.5 if wrong_line else 8.5,
                    over_odds=-125, under_odds=-105)
    edge = EdgeResult(id=1, game_id=1, prediction_id=1, odds_id=1,
                      calculated_at=alert.alert_time - timedelta(minutes=1), book_total=8.5)
    trade.edge_result_id = 1
    db.add_all([odds, edge])
    db.commit()
    result = backfill_finished_paper_bets(db)
    assert trade.status == "settled" and trade.result == "win"
    if not future and not wrong_line:
        assert trade.odds == -125 and trade.profit_loss == 80
        assert result["paper_trades"]["repaired_prices"] == 1
        assert get_paper_summary(db)["all_time_roi"] == .8
    else:
        assert trade.odds is None and trade.profit_loss is None
        assert get_paper_summary(db)["unpriced_settled_trades"] == 1
        assert get_paper_summary(db)["priced_settled_trades"] == 0
    assert backfill_finished_paper_bets(db)["paper_trades"]["repaired_prices"] == 0


def test_stored_final_settlement_survives_schedule_fetch_failure(db):
    _, _, trade, order, _ = seed(db)
    db.add(Game(game_id=2, game_date=date.today(), season=2026, away_team="A", home_team="H"))
    db.add(Prediction(game_id=2, model_version="test", away_win_pct=.5, home_win_pct=.5,
                      projected_away_score=4, projected_home_score=5, projected_total=9, confidence_score=0))
    db.commit()
    with patch("app.services.review_service.fetch_schedule_for_date", side_effect=RuntimeError("offline")):
        with pytest.raises(RuntimeError):
            resolve_completed_games(db)
    assert trade.status == order.status == "settled"
