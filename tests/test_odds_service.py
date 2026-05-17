from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.schema import Game, GameOdds, SnapshotType
from app.services.odds_service import _event_game_date, _match_game, compute_line_movement, fetch_and_store_odds


class _FakeResponse:
    def __init__(self, payload: list[dict]) -> None:
        self._payload = payload
        self.content = str(payload).encode("utf-8")

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict]:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, payload: list[dict]) -> None:
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return _FakeResponse(self.payload)


class OddsServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        TestingSessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = TestingSessionLocal()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _game(self, game_id: int, game_date: date, away: str, home: str) -> Game:
        game = Game(
            game_id=game_id,
            game_date=game_date,
            season=2026,
            away_team=away,
            home_team=home,
            away_team_id=1,
            home_team_id=2,
            venue="Test Park",
            status="Preview",
            start_time="2026-04-02T18:00:00+00:00",
        )
        self.db.add(game)
        self.db.commit()
        return game

    def _odds(
        self,
        game_id: int,
        *,
        sportsbook: str = "draftkings",
        snapshot_type: SnapshotType = SnapshotType.open,
        fetched_at: datetime | None = None,
        away_ml: int = 110,
        home_ml: int = -130,
        total_line: float = 8.5,
    ) -> GameOdds:
        odds = GameOdds(
            game_id=game_id,
            sportsbook=sportsbook,
            snapshot_type=snapshot_type,
            fetched_at=fetched_at or datetime.now(timezone.utc),
            away_ml=away_ml,
            home_ml=home_ml,
            total_line=total_line,
            over_odds=-110,
            under_odds=-110,
        )
        self.db.add(odds)
        self.db.commit()
        self.db.refresh(odds)
        return odds

    def test_event_game_date_uses_et(self) -> None:
        event = {"commence_time": "2026-04-03T01:45:00Z"}
        self.assertEqual(_event_game_date(event), date(2026, 4, 2))

    async def test_fetch_and_store_reuses_fresh_existing_rows(self) -> None:
        self._game(1, date(2026, 4, 2), "Toronto Blue Jays", "Chicago White Sox")
        existing = self._odds(
            1,
            fetched_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        payload = [{
            "away_team": "Toronto Blue Jays",
            "home_team": "Chicago White Sox",
            "commence_time": "2026-04-02T20:10:00Z",
            "bookmakers": [{
                "key": "draftkings",
                "markets": [
                    {"key": "h2h", "outcomes": [
                        {"name": "Toronto Blue Jays", "price": 110},
                        {"name": "Chicago White Sox", "price": -130},
                    ]},
                    {"key": "totals", "outcomes": [
                        {"name": "Over", "price": -110, "point": 8.5},
                        {"name": "Under", "price": -110, "point": 8.5},
                    ]},
                ],
            }],
        }]

        with patch("app.services.odds_service.httpx.AsyncClient", return_value=_FakeAsyncClient(payload)):
            rows = await fetch_and_store_odds(self.db, SnapshotType.open, books=["draftkings"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, existing.id)

    async def test_fetch_and_store_keeps_new_rows_when_other_rows_are_duplicates(self) -> None:
        self._game(1, date(2026, 4, 2), "Toronto Blue Jays", "Chicago White Sox")
        self._game(2, date(2026, 4, 2), "Minnesota Twins", "Kansas City Royals")
        self._odds(1, fetched_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        payload = [
            {
                "away_team": "Toronto Blue Jays",
                "home_team": "Chicago White Sox",
                "commence_time": "2026-04-02T20:10:00Z",
                "bookmakers": [{
                    "key": "draftkings",
                    "markets": [{"key": "h2h", "outcomes": [
                        {"name": "Toronto Blue Jays", "price": 110},
                        {"name": "Chicago White Sox", "price": -130},
                    ]}],
                }],
            },
            {
                "away_team": "Minnesota Twins",
                "home_team": "Kansas City Royals",
                "commence_time": "2026-04-02T18:10:00Z",
                "bookmakers": [{
                    "key": "fanduel",
                    "markets": [{"key": "h2h", "outcomes": [
                        {"name": "Minnesota Twins", "price": 120},
                        {"name": "Kansas City Royals", "price": -142},
                    ]}],
                }],
            },
        ]

        with patch("app.services.odds_service.httpx.AsyncClient", return_value=_FakeAsyncClient(payload)):
            rows = await fetch_and_store_odds(self.db, SnapshotType.open, books=["draftkings", "fanduel"])

        self.assertEqual({row.game_id for row in rows}, {1, 2})
        persisted = self.db.query(GameOdds).filter(GameOdds.snapshot_type == SnapshotType.open).all()
        self.assertEqual(len(persisted), 2)
        self.assertEqual({row.sportsbook for row in persisted}, {"draftkings", "fanduel"})

    async def test_fetch_and_store_refreshes_stale_existing_rows(self) -> None:
        self._game(1, date(2026, 4, 2), "Toronto Blue Jays", "Chicago White Sox")
        existing = self._odds(
            1,
            fetched_at=datetime.now(timezone.utc) - timedelta(hours=6),
        )
        payload = [{
            "away_team": "Toronto Blue Jays",
            "home_team": "Chicago White Sox",
            "commence_time": "2026-04-02T20:10:00Z",
            "bookmakers": [{
                "key": "draftkings",
                "markets": [
                    {"key": "h2h", "outcomes": [
                        {"name": "Toronto Blue Jays", "price": 125},
                        {"name": "Chicago White Sox", "price": -145},
                    ]},
                    {"key": "totals", "outcomes": [
                        {"name": "Over", "price": -105, "point": 9.0},
                        {"name": "Under", "price": -115, "point": 9.0},
                    ]},
                ],
            }],
        }]

        with patch("app.services.odds_service.httpx.AsyncClient", return_value=_FakeAsyncClient(payload)):
            rows = await fetch_and_store_odds(self.db, SnapshotType.open, books=["draftkings"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, existing.id)
        self.db.refresh(existing)
        self.assertEqual(existing.away_ml, 125)
        self.assertEqual(existing.home_ml, -145)
        self.assertEqual(float(existing.total_line), 9.0)
        self.assertEqual(existing.over_odds, -105)
        self.assertEqual(existing.under_odds, -115)

    def test_match_game_normalizes_safe_team_name_variants(self) -> None:
        game = self._game(3, date(2026, 4, 2), "St. Louis Cardinals", "Kansas City Royals")
        matched, reason = _match_game(self.db, {
            "away_team": "St Louis Cardinals",
            "home_team": "Kansas City Royals",
            "commence_time": "2026-04-02T18:10:00Z",
        })

        self.assertIsNotNone(matched)
        self.assertEqual(matched.game_id, game.game_id)
        self.assertIsNone(reason)

    def test_compute_line_movement_uses_multibook_consensus(self) -> None:
        self._game(4, date.today(), "Toronto Blue Jays", "Chicago White Sox")
        for book, open_away, open_home, pre_away, pre_home in [
            ("draftkings", 130, -150, 105, -125),
            ("fanduel", 128, -148, 100, -120),
        ]:
            self._odds(
                4,
                sportsbook=book,
                snapshot_type=SnapshotType.open,
                away_ml=open_away,
                home_ml=open_home,
                total_line=8.0,
            )
            self._odds(
                4,
                sportsbook=book,
                snapshot_type=SnapshotType.pregame,
                away_ml=pre_away,
                home_ml=pre_home,
                total_line=8.5,
            )

        movement = compute_line_movement(self.db, 4)

        self.assertIsNotNone(movement)
        self.assertEqual(movement.sportsbook, "consensus")
        self.assertGreater(float(movement.away_prob_move), 0.04)
        self.assertTrue(movement.sharp_away)
        self.assertTrue(movement.total_steam_over)


if __name__ == "__main__":
    unittest.main()
