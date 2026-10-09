from datetime import date, datetime, timezone
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.schema import Game, GameOdds, OddsApiRequestLog, SnapshotType
from app.services.market_source_service import build_market_source_report
from app.services.odds_service import get_odds_provider_pause


class MarketSourceServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_expired_exhaustion_does_not_label_provider_paused(self):
        self.db.add(OddsApiRequestLog(
            provider="the_odds_api", endpoint="odds", status="error",
            requested_at=datetime(2026, 8, 14, 23, tzinfo=timezone.utc),
            error="OUT_OF_USAGE_CREDITS",
        ))
        self.db.commit()
        quota = {"provider_out_of_usage_credits": True, "provider_status": "OUT_OF_USAGE_CREDITS"}
        for now, expected in ((datetime(2026, 8, 14, 23, 30, tzinfo=timezone.utc), "PAUSED"),
                              (datetime(2026, 8, 15, tzinfo=timezone.utc), "OK")):
            with self.subTest(now=now), \
                 patch("app.routes.debug.build_odds_quota_report", return_value=quota), \
                 patch.multiple("app.services.odds_service",
                                ODDS_API_QUOTA_RESET_DAY=15,
                                ODDS_API_QUOTA_EXHAUSTED_PAUSE_UNTIL_MONTH_RESET=True), \
                 patch("app.services.market_source_service.get_odds_provider_pause",
                       side_effect=lambda db: get_odds_provider_pause(db, now=now)):
                report = build_market_source_report(self.db, report_date=date(2026, 8, 15))
            self.assertEqual(report["sources"][0]["status"], expected)

    def test_default_validation_date_uses_eastern_day(self):
        now_utc = datetime(2026, 8, 20, 1, tzinfo=timezone.utc)
        with patch("app.routes.debug.build_odds_quota_report", return_value={}), \
             patch("app.services.market_source_service.datetime") as clock, \
             patch("app.services.market_source_service.get_report_snapshot", return_value=None) as snapshot:
            clock.now.side_effect = lambda zone: now_utc.astimezone(zone)
            report = build_market_source_report(self.db)
            self.assertEqual(snapshot.call_args.kwargs["report_date"], date(2026, 8, 19))
            build_market_source_report(self.db, report_date=date(2026, 8, 18))
            self.assertEqual(snapshot.call_args.kwargs["report_date"], date(2026, 8, 18))
        self.assertEqual(report["stored_observations_by_source"], {})
        self.assertIsNone(report["sources"][1]["match_rate"])

    def test_source_counts_and_validation_with_zero_checked_events(self):
        self.db.add(Game(game_id=1, game_date=date(2026, 8, 19), season=2026,
                         away_team="Away", home_team="Home"))
        self.db.add_all([
            GameOdds(game_id=1, sportsbook="draftkings", snapshot_type=SnapshotType.open),
            GameOdds(game_id=1, sportsbook="draftkings", snapshot_type=SnapshotType.pregame),
            GameOdds(game_id=1, sportsbook="fanduel", snapshot_type=SnapshotType.open),
        ])
        self.db.commit()
        with patch("app.routes.debug.build_odds_quota_report", return_value={}), \
             patch("app.services.market_source_service.get_report_snapshot",
                   return_value={"events_checked": 0, "counts": {}}):
            report = build_market_source_report(self.db, report_date=date(2026, 8, 19))
        self.assertEqual(report["stored_observations_by_source"], {"draftkings": 2, "fanduel": 1})
        self.assertEqual(report["sources"][0]["observations"], 3)
        self.assertEqual(report["sources"][1]["status"], "SCOUTING")
        self.assertIsNone(report["sources"][1]["match_rate"])
