"""Verify the production settlement code ONLY in a disposable in-memory SQLite DB.

python analysis/verify_export_settlement.py
The export has no executions: no real fills/bankroll data are fabricated here.
"""
from __future__ import annotations

import csv
import json
import os
import socket
import sys
from contextlib import ExitStack
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["PYTHON_DOTENV_DISABLED"] = "1"


def deny(*args, **kwargs):
    raise AssertionError("This verification is offline.")


def main():
    with ExitStack() as guards:
        guards.enter_context(patch("dotenv.load_dotenv", return_value=False))
        guards.enter_context(patch.object(socket, "create_connection", side_effect=deny))
        guards.enter_context(patch.object(socket, "getaddrinfo", side_effect=deny))
        from sqlalchemy import Boolean, Date, DateTime, Float, Integer, Numeric, create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db import Base
        from app.models.schema import BetAlert, EdgeResult, Game, GameOdds, GameOutcomeReview, PaperTrade, Prediction
        from app.models.betting import BetOrder, BetExecution, BankrollSnapshot
        from app.services.paper_settlement_service import backfill_finished_paper_bets
        from app.services.paper_trade_service import get_paper_summary

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine)() as db:
            for model in [Game, Prediction, GameOdds, EdgeResult, BetAlert, PaperTrade, GameOutcomeReview]:
                table = model.__table__
                with (ROOT / "_season_export" / (table.name + ".csv")).open(encoding="utf-8", newline="") as source:
                    records = []
                    for raw in csv.DictReader(source):
                        record = {}
                        for column in table.columns:
                            if column.name not in raw:
                                continue
                            value = raw[column.name]
                            if value == "":
                                value = None
                            elif isinstance(column.type, Boolean):
                                value = value.lower() in {"true", "1"}
                            elif isinstance(column.type, DateTime):
                                value = datetime.fromisoformat(value)
                            elif isinstance(column.type, Date):
                                value = date.fromisoformat(value)
                            elif isinstance(column.type, Integer):
                                value = int(value)
                            elif isinstance(column.type, (Numeric, Float)):
                                value = float(value)
                            record[column.name] = value
                        records.append(record)
                    db.execute(table.insert(), records)
            db.commit()
            before = get_paper_summary(db)
            first = backfill_finished_paper_bets(db)
            second = backfill_finished_paper_bets(db)
            after = get_paper_summary(db)
            assert before["paper_bets_placed"] == after["paper_bets_placed"] == 208
            assert before["settled_trades"] == 40
            assert db.query(PaperTrade).filter(PaperTrade.status == "settled").count() == 208
            assert after["open_trades"] == 0 and after["settled_trades"] == 208
            assert after["priced_settled_trades"] == 170 and after["unpriced_settled_trades"] == 38
            assert second["paper_trades"]["settled"] == second["paper_trades"]["repaired_prices"] == 0
            report = {"database": "in-memory SQLite only", "first_backfill": first, "second_backfill": second,
                      "before": before, "after": after,
                      "execution_limit": "No bet_executions or bankroll snapshots exported; orders validated by unit tests and score audit only."}
            (ROOT / "analysis" / "settlement_export_verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({"first": first, "second": second, "after_settled": after["settled_trades"],
                              "after_priced": after["priced_settled_trades"], "after_unpriced": after["unpriced_settled_trades"]}))
        engine.dispose()


if __name__ == "__main__":
    main()
