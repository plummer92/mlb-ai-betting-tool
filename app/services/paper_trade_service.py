from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.schema import BetAlert, EdgeResult, PaperTrade

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
    if result != "win" or odds is None:
        return None
    if odds > 0:
        return (stake * Decimal(odds) / Decimal(100)).quantize(Decimal("0.01"))
    return (stake * Decimal(100) / Decimal(abs(odds))).quantize(Decimal("0.01"))


def _roi(staked: Decimal, profit_loss: Decimal) -> float:
    if staked == 0:
        return 0.0
    return round(float(profit_loss / staked), 4)


def log_alert_as_paper_trade(db: Session, alert: BetAlert, edge: EdgeResult | None = None) -> None:
    """
    Best-effort alert mirror. This must never block Discord alerts.
    """
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
            db.add(
                PaperTrade(
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
                    odds=_odds_for_play(edge, play),
                    line=_line_for_play(edge, play),
                    status="open",
                )
            )
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
        try:
            stake = DEFAULT_PAPER_STAKE
            result = alert.bet_result if alert.bet_result in {"win", "loss", "push"} else None
            odds = _odds_for_play(edge, alert.play)
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
                    line=_line_for_play(edge, alert.play),
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
            by_confidence[confidence]["staked"] += stake
            if profit_loss is not None:
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
                daily[trade.game_date]["staked"] += stake
                if profit_loss is not None:
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
        "all_time_roi": _roi(total_staked, total_pl),
        "all_time_profit_loss": round(float(total_pl), 2),
        "roi_by_confidence": confidence_rows,
        "win_rate_by_play_type": play_rows,
        "rolling_7_day_roi": rolling,
    }
