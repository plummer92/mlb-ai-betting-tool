from datetime import datetime
from zoneinfo import ZoneInfo

from app.db import SessionLocal
from app.services.admin_service import backfill_prediction_dashboard_metrics

ET = ZoneInfo("America/New_York")


def main() -> None:
    db = SessionLocal()
    try:
        result = backfill_prediction_dashboard_metrics(
            db,
            target_date=datetime.now(ET).date(),
            active_only=True,
        )
        print(result)
    finally:
        db.close()


if __name__ == "__main__":
    main()
