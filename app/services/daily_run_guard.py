"""Durable date ownership; never replay ambiguous external side effects."""
import json
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.schema import DailyPipelineRun


class DailyRunGuard:
    def __init__(self, db: Session, target_date: date):
        self.bind = db.get_bind()
        self.target_date = target_date

    def _payload(self, run: DailyPipelineRun) -> dict:
        if run.status == "completed":
            return json.loads(run.result_json)
        details = {
            "running": "already running; check daily-run/status for completion",
            "failed": "failed before external side effects; safe to retry",
            "blocked": "external side effects may have occurred; operator review required",
        }
        return {
            "date": str(self.target_date),
            "status": run.status,
            "detail": details[run.status],
        }

    def status(self) -> dict:
        with Session(bind=self.bind) as state_db:
            run = state_db.get(DailyPipelineRun, self.target_date)
            if run is None:
                return {"date": str(self.target_date), "status": "not_found"}
            return self._payload(run)

    def claim(self) -> dict | None:
        """None means ownership. Insert/CAS commits serialize API workers."""
        with Session(bind=self.bind) as state_db:
            state_db.add(DailyPipelineRun(run_date=self.target_date, status="running"))
            try:
                state_db.commit()
                return None
            except IntegrityError:
                state_db.rollback()

            claimed = (
                state_db.query(DailyPipelineRun)
                .filter(
                    DailyPipelineRun.run_date == self.target_date,
                    DailyPipelineRun.status == "failed",
                    DailyPipelineRun.side_effects_started == False,  # noqa: E712
                )
                .update({"status": "running"}, synchronize_session=False)
            )
            state_db.commit()
            if claimed:
                return None
            return self._payload(state_db.get(DailyPipelineRun, self.target_date))

    def start_side_effects(self) -> None:
        # Separate session: durable BEFORE sending Discord or spending odds quota.
        with Session(bind=self.bind) as state_db:
            run = state_db.get(DailyPipelineRun, self.target_date)
            run.side_effects_started = True
            state_db.commit()

    def complete(self, result: dict) -> None:
        with Session(bind=self.bind) as state_db:
            run = state_db.get(DailyPipelineRun, self.target_date)
            run.result_json = json.dumps(result)
            run.status = "completed"
            state_db.commit()

    def fail(self) -> None:
        with Session(bind=self.bind) as state_db:
            run = state_db.get(DailyPipelineRun, self.target_date)
            run.status = "blocked" if run.side_effects_started else "failed"
            state_db.commit()
