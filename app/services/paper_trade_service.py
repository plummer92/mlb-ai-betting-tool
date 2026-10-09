from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.schema import BetAlert, EdgeResult, Game, GameOdds, PaperTrade
from app.services.paper_settlement_service import final_scores, paper_outcome

DEFAULT_PAPER_STAKE = Decimal("100.00")


def _odds_for_play(edge: EdgeResult | None, play: str) -> int | None:
    if edge is None:
        return None
    if play == "away_ml":
        return edge.away_ml
    if play == "home_ml":
        return edge.home_ml
    if play == "over":
        return edge.over_odds
    if play == "under":
        return edge.under_odds
    return None


def _line_for_play(edge: EdgeResult | None, play: str) -> Decimal | None:
    if edge is None or play not in {"over", "under"}:
        return None
    return edge.book_total


def _profit_loss(result: str | None, odds: int | None, stake: Decimal) -> Decimal | None:
    if result is None:
        return None
    if result == "push":
        return Decimal("0.00")
    if result == "loss":
        return -stake
    if result != "win" or odds is None or abs(odds) < 100:
        return None
    if odds > 0:
        return (stake * Decimal(odds) / Decimal(100)).quantize(Decimal("0.01"))
    return (stake * Decimal(100) / Decimal(abs(odds))).quantize(Decimal("0.01"))


def _roi(staked: Decimal, profit_loss: Decimal) -> float:
    if staked == 0:
        return 0.0
    return round(float(profit_loss / staked), 4)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _recorded_terms(db: Session, alert: BetAlert | None, edge: EdgeResult | None, play: str, line):
    """Recover only an exact linked price available at the original alert time.

    Historical mirror creation time is not entry time. Never substitute latest
    odds or guess -110 for a missing historical price.
    """
    if alert is None or edge is None or alert.alert_time is None or edge.game_id != alert.game_id:
        return None, line
    cutoff = _as_utc(alert.alert_time)
    if edge.calculated_at is not None and _as_utc(edge.calculated_at) <= cutoff:
        edge_line = _line_for_play(edge, play)
        if play not in {"over", "under"} or line is None or edge_line == line:
            price = _odds_for_play(edge, play)
            if price is not None:
                return price, edge_line if line is None else line
    odds = db.get(GameOdds, edge.odds_id) if edge.odds_id is not None else None
    if odds is None or odds.game_id != alert.game_id or odds.fetched_at is None or _as_utc(odds.fetched_at) > cutoff:
        return None, line
    if play in {"over", "under"} and line is not None and odds.total_line != line:
        return None, line
    price = {"home_ml": odds.home_ml, "away_ml": odds.away_ml,
             "over": odds.over_odds, "under": odds.under_odds}.get(play)
    return price, odds.total_line if play in {"over", "under"} and line is None else line


def settle_finished_paper_trades(db: Session) -> dict:
    """Persist alert mirrors from final scores at their own recorded price/line.

    No date window, prediction/review requirement, or network calls. Locks open
    rows on databases supporting row locks; preserves settled outcomes.
    """
    rows = (
        db.query(PaperTrade)
        .filter(PaperTrade.status.in_(["open", "settled"]))
        .order_by(PaperTrade.id)
        .populate_existing().with_for_update().all()
    )
    settled = 0
    repaired_prices = 0
    unpriced = 0
    skipped = 0
    for trade in rows:
        scores = final_scores(db, db.get(Game, trade.game_id))
        if scores is None:
            skipped += 1
            continue
        stake = Decimal(trade.paper_stake) if trade.paper_stake is not None else None
        if stake is None or stake <= 0:
            skipped += 1
            continue
        if trade.odds is None or (trade.play in {"over", "under"} and trade.line is None):
            alert = db.get(BetAlert, trade.bet_alert_id)
            edge = db.get(EdgeResult, trade.edge_result_id) if trade.edge_result_id else None
            price, line = _recorded_terms(db, alert, edge, trade.play, trade.line)
            if trade.odds is None and price is not None and abs(price) >= 100:
                trade.odds = price
                repaired_prices += 1
            if trade.line is None:
                trade.line = line
        outcome = paper_outcome(trade.play, *scores, trade.line)
        if outcome is None:
            skipped += 1
            continue
        if trade.status == "settled":
            # Repair missing price/P&L only; never change a settled outcome.
            if trade.result == outcome and trade.profit_loss is None:
                trade.profit_loss = _profit_loss(outcome, trade.odds, stake)
            continue
        if trade.result is not None:
            skipped += 1
            continue
        trade.result = outcome
        trade.profit_loss = _profit_loss(outcome, trade.odds, stake)
        trade.status = "settled"
        trade.settled_at = datetime.now(timezone.utc)
        settled += 1
        if trade.odds is None or abs(trade.odds) < 100:
            unpriced += 1
    db.commit()
    return {"settled": settled, "skipped": skipped, "repaired_prices": repaired_prices,
            "settled_without_price": unpriced}


def log_alert_as_paper_trade(db: Session, alert: BetAlert, edge: EdgeResult | None = None) -> None:
    """
    Best-effort alert mirror. This must never block Discord alerts.
    """
    from app.services.prospective_gate_service import gate_allows_action
    if not gate_allows_action(db, game_id=alert.game_id, play=alert.play, action="paper_mirror",
                              sportsbook=edge.sportsbook if edge else None):
        return
    try:
        if alert.id is None:
            db.flush()

        nested = db.begin_nested()
        try:
            existing = db.query(PaperTrade.id).filter(PaperTrade.bet_alert_id == alert.id).first()
            if existing:
                nested.commit()
                return

            play = alert.play or "none"
            price, line = _recorded_terms(db, alert, edge, play, _line_for_play(edge, play))
            trade = PaperTrade(
                    bet_alert_id=alert.id,
                    game_id=alert.game_id,
                    prediction_id=alert.prediction_id,
                    edge_result_id=alert.edge_result_id,
                    game_date=alert.game_date,
                    play=play,
                    confidence=alert.confidence,
                    edge_pct=alert.edge_pct,
                    ev=alert.ev,
                    paper_stake=DEFAULT_PAPER_STAKE,
                    odds=price,
                    line=line,
                    status="open",
                )
            db.add(trade)
            db.flush()
            from app.services.history_service import append_decision
            append_decision(db, dict(game_id=alert.game_id, edge_result_id=alert.edge_result_id,
                play=play, sportsbook=edge.sportsbook if edge else None,
                line=float(line) if line is not None else None, paper_trade_id=trade.id,
                decision_status="PAPER", reason="alert_paper_mirror", action="paper_placement",
                entry_time=alert.alert_time.isoformat() if alert.alert_time else None))
            db.flush()
            nested.commit()
        except Exception as exc:
            nested.rollback()
            print(f"[paper] alert paper-trade log skipped: {exc}", flush=True)
    except Exception as exc:
        print(f"[paper] alert paper-trade log skipped: {exc}", flush=True)


def backfill_missing_paper_trades(db: Session, limit: int | None = None) -> dict:
    """
    Create paper-trade mirror rows for historical BetAlert records.

    This is intentionally idempotent so it is safe to run manually after deploys
    or any time the tracker looks out of sync.
    """
    query = (
        db.query(BetAlert, EdgeResult)
        .outerjoin(PaperTrade, PaperTrade.bet_alert_id == BetAlert.id)
        .outerjoin(EdgeResult, EdgeResult.id == BetAlert.edge_result_id)
        .filter(PaperTrade.id.is_(None))
        .order_by(BetAlert.game_date.asc(), BetAlert.id.asc())
    )
    if limit is not None:
        query = query.limit(limit)

    created = 0
    skipped = 0
    errors: list[str] = []

    for alert, edge in query.all():
        from app.services.prospective_gate_service import gate_allows_action
        if not gate_allows_action(db, game_id=alert.game_id, play=alert.play, action="paper_backfill"):
            skipped += 1
            continue
        try:
            stake = DEFAULT_PAPER_STAKE
            result = alert.bet_result if alert.bet_result in {"win", "loss", "push"} else None
            odds, line = _recorded_terms(db, alert, edge, alert.play, _line_for_play(edge, alert.play))
            profit_loss = _profit_loss(result, odds, stake)
            db.add(
                PaperTrade(
                    bet_alert_id=alert.id,
                    game_id=alert.game_id,
                    prediction_id=alert.prediction_id,
                    edge_result_id=alert.edge_result_id,
                    game_date=alert.game_date,
                    play=alert.play or "none",
                    confidence=alert.confidence,
                    edge_pct=alert.edge_pct,
                    ev=alert.ev,
                    paper_stake=stake,
                    odds=odds,
                    line=line,
                    status="settled" if result else "open",
                    result=result,
                    profit_loss=profit_loss,
                    settled_at=alert.resolved_at if result else None,
                )
            )
            created += 1
        except Exception as exc:
            skipped += 1
            if len(errors) < 10:
                errors.append(f"alert_id={alert.id}: {exc}")

    db.commit()
    return {
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "remaining_missing": max((db.query(func.count(BetAlert.id)).scalar() or 0) - (db.query(func.count(PaperTrade.id)).scalar() or 0), 0),
    }


def get_paper_summary(db: Session) -> dict:
    total_alerts = db.query(func.count(BetAlert.id)).scalar() or 0
    rows = (
        db.query(PaperTrade, BetAlert)
        .outerjoin(BetAlert, BetAlert.id == PaperTrade.bet_alert_id)
        .order_by(PaperTrade.game_date.asc(), PaperTrade.id.asc())
        .all()
    )

    by_confidence: dict[str, dict[str, Decimal | int]] = defaultdict(
        lambda: {"bets": 0, "settled": 0, "staked": Decimal("0.00"), "profit_loss": Decimal("0.00")}
    )
    by_play: dict[str, dict[str, int]] = defaultdict(lambda: {"bets": 0, "wins": 0, "losses": 0, "pushes": 0})

    today = datetime.now(timezone.utc).date()
    rolling_start = today - timedelta(days=6)
    daily: dict[date, dict[str, Decimal | int]] = {
        rolling_start + timedelta(days=i): {"bets": 0, "settled": 0, "staked": Decimal("0.00"), "profit_loss": Decimal("0.00")}
        for i in range(7)
    }

    open_trades = 0
    settled_trades = 0
    total_pl = Decimal("0.00")
    total_staked = Decimal("0.00")
    unpriced_settled_trades = 0

    for trade, alert in rows:
        result = trade.result or (alert.bet_result if alert else None)
        stake = Decimal(trade.paper_stake or DEFAULT_PAPER_STAKE)
        profit_loss = trade.profit_loss
        if profit_loss is None:
            profit_loss = _profit_loss(result, trade.odds, stake)

        confidence = trade.confidence or "unknown"
        play = trade.play or "unknown"
        by_confidence[confidence]["bets"] += 1
        by_play[play]["bets"] += 1

        if result in {"win", "loss", "push"}:
            settled_trades += 1
            by_confidence[confidence]["settled"] += 1
            priced = trade.odds is not None and abs(trade.odds) >= 100 and profit_loss is not None
            if not priced:
                unpriced_settled_trades += 1
            if priced:
                by_confidence[confidence]["staked"] += stake
                by_confidence[confidence]["profit_loss"] += profit_loss
                total_pl += profit_loss
                total_staked += stake

            if result == "win":
                by_play[play]["wins"] += 1
            elif result == "loss":
                by_play[play]["losses"] += 1
            elif result == "push":
                by_play[play]["pushes"] += 1

            if rolling_start <= trade.game_date <= today:
                daily[trade.game_date]["settled"] += 1
                if priced:
                    daily[trade.game_date]["staked"] += stake
                    daily[trade.game_date]["profit_loss"] += profit_loss
        else:
            open_trades += 1

        if rolling_start <= trade.game_date <= today:
            daily[trade.game_date]["bets"] += 1

    confidence_rows = []
    for confidence, stats in sorted(by_confidence.items()):
        staked = stats["staked"]
        pl = stats["profit_loss"]
        confidence_rows.append({
            "confidence": confidence,
            "bets": stats["bets"],
            "settled": stats["settled"],
            "profit_loss": round(float(pl), 2),
            "roi": _roi(staked, pl),
        })

    play_rows = []
    for play, stats in sorted(by_play.items()):
        decisions = stats["wins"] + stats["losses"]
        play_rows.append({
            "play": play,
            "bets": stats["bets"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "pushes": stats["pushes"],
            "win_rate": round(stats["wins"] / decisions, 4) if decisions else 0.0,
        })

    rolling = []
    for day in sorted(daily):
        stats = daily[day]
        staked = stats["staked"]
        pl = stats["profit_loss"]
        rolling.append({
            "date": day.isoformat(),
            "bets": stats["bets"],
            "settled": stats["settled"],
            "profit_loss": round(float(pl), 2),
            "roi": _roi(staked, pl),
        })

    return {
        "paper_bets_placed": len(rows),
        "actual_bet_alerts": total_alerts,
        "missing_paper_trades": max(total_alerts - len(rows), 0),
        "open_trades": open_trades,
        "settled_trades": settled_trades,
        "priced_settled_trades": settled_trades - unpriced_settled_trades,
        "unpriced_settled_trades": unpriced_settled_trades,
        "roi_scope": "settled trades with recorded or safely recovered odds only",
        "all_time_roi": _roi(total_staked, total_pl),
        "all_time_profit_loss": round(float(total_pl), 2),
        "roi_by_confidence": confidence_rows,
        "win_rate_by_play_type": play_rows,
        "rolling_7_day_roi": rolling,
    }
