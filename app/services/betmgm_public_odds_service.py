from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy.orm import Session

from app.models.schema import Game, GameOdds, SnapshotType

BETMGM_PUBLIC_MLB_URL = "https://www.betmgm.com/en/sports/baseball-23/betting/usa-9/mlb-75"
BETMGM_PUBLIC_SPORTSBOOK = "betmgm_public"
BETMGM_BASEBALL_SPORT_ID = 23
BETMGM_MLB_COMPETITION_ID = 75
BETMGM_PUBLIC_ACCESS_ID_FALLBACK = "ZTg4YWEwMTgtZTlhYy00MWRkLWIzYWYtZjMzODI5ZDE0Mjc5"
BETMGM_CDS_API_URL_FALLBACK = "https://cf-us3-cds-api.itsfogo.com"
MLB_TOTAL_MIN = 5.0
MLB_TOTAL_MAX = 13.5
MAX_MAIN_TOTAL_PRICE_ABS = 350
TOTAL_MATCH_TOLERANCE = 0.5
MONEYLINE_MATCH_TOLERANCE = 35
TOTAL_PRICE_MATCH_TOLERANCE = 45

_PUBLIC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
)

_TEAM_ALIASES: dict[str, str] = {
    "angels": "Los Angeles Angels",
    "astros": "Houston Astros",
    "athletics": "Athletics",
    "a's": "Athletics",
    "blue jays": "Toronto Blue Jays",
    "braves": "Atlanta Braves",
    "brewers": "Milwaukee Brewers",
    "cardinals": "St. Louis Cardinals",
    "cubs": "Chicago Cubs",
    "diamondbacks": "Arizona Diamondbacks",
    "dbacks": "Arizona Diamondbacks",
    "dodgers": "Los Angeles Dodgers",
    "giants": "San Francisco Giants",
    "guardians": "Cleveland Guardians",
    "mariners": "Seattle Mariners",
    "marlins": "Miami Marlins",
    "mets": "New York Mets",
    "nationals": "Washington Nationals",
    "orioles": "Baltimore Orioles",
    "padres": "San Diego Padres",
    "phillies": "Philadelphia Phillies",
    "pirates": "Pittsburgh Pirates",
    "rangers": "Texas Rangers",
    "rays": "Tampa Bay Rays",
    "red sox": "Boston Red Sox",
    "reds": "Cincinnati Reds",
    "rockies": "Colorado Rockies",
    "royals": "Kansas City Royals",
    "tigers": "Detroit Tigers",
    "twins": "Minnesota Twins",
    "white sox": "Chicago White Sox",
    "yankees": "New York Yankees",
}

_TEAM_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(alias) for alias in sorted(_TEAM_ALIASES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_SPREAD_RE = re.compile(r"^(?P<point>[+-]\d+(?:\.\d+)?)\s+(?P<decimal>\d+(?:\.\d+)?)$")
_TOTAL_RE = re.compile(r"^(?P<side>[OU])\s+(?P<line>\d+(?:\.\d+)?)\s+(?P<decimal>\d+(?:\.\d+)?)$", re.IGNORECASE)
_DECIMAL_RE = re.compile(r"^\d+(?:\.\d+)?$")


def _normalize_name(value: str | None) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in (value or ""))
    return " ".join(normalized.split())


def decimal_to_american(decimal_odds: float | None) -> int | None:
    if decimal_odds is None or decimal_odds <= 1.0:
        return None
    if decimal_odds >= 2.0:
        return int(round((decimal_odds - 1.0) * 100))
    return int(round(-100.0 / (decimal_odds - 1.0)))


def parse_betmgm_public_page_text(text: str, *, source_url: str = BETMGM_PUBLIC_MLB_URL) -> dict[str, Any]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[tuple[str, str, int]] = set()

    for idx, line in enumerate(lines):
        teams = _teams_in_line(line)
        if len(teams) < 2:
            continue
        if not _looks_like_game_row(lines, idx):
            continue

        away_team, home_team = teams[0], teams[1]
        window = _event_window(lines, idx)
        parsed = _parse_market_window(window)
        if not parsed["has_any_market"]:
            warnings.append(f"Skipped {away_team} @ {home_team}: no complete public market prices near row.")
            continue

        key = (away_team, home_team, idx)
        if key in seen:
            continue
        seen.add(key)
        events.append({
            "sportsbook": BETMGM_PUBLIC_SPORTSBOOK,
            "away_team_hint": away_team,
            "home_team_hint": home_team,
            "event_label": line,
            "row_index": idx,
            "source_url": source_url,
            "market_format": "decimal_public_page",
            **parsed,
        })

    status = "ok" if events else "no_public_odds_found"
    return {
        "status": status,
        "provider": BETMGM_PUBLIC_SPORTSBOOK,
        "source_url": source_url,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "events_count": len(events),
        "events": events,
        "warnings": warnings[:25],
    }


async def probe_betmgm_public_odds(
    *,
    render: bool = False,
    use_cds: bool = True,
    fixture_limit: int = 15,
    timeout_seconds: float = 18.0,
) -> dict[str, Any]:
    if use_cds:
        cds_payload = await _probe_public_cds_odds(
            fixture_limit=fixture_limit,
            timeout_seconds=timeout_seconds,
        )
        if cds_payload.get("events_count", 0) > 0 or cds_payload.get("status") not in {"cds_error", "cds_no_fixtures"}:
            return cds_payload

    static_result = await _fetch_static_page_text(timeout_seconds=timeout_seconds)
    static_payload = parse_betmgm_public_page_text(static_result.get("text") or "")
    static_payload["source_mode"] = "static_page"
    static_payload["static_fetch"] = {
        key: value for key, value in static_result.items() if key != "text"
    }
    if static_payload["events_count"] > 0 or not render:
        if static_payload["events_count"] == 0 and static_result.get("status") == "ok":
            static_payload["status"] = "needs_rendered_page"
            static_payload["warnings"].append(
                "BetMGM returned the app shell without visible odds in static HTML; try render=true for a browser-based scout."
            )
        return static_payload

    rendered_result = await _fetch_rendered_page_text(timeout_seconds=timeout_seconds)
    rendered_payload = parse_betmgm_public_page_text(rendered_result.get("text") or "")
    rendered_payload["source_mode"] = "rendered_page"
    rendered_payload["static_fetch"] = {
        key: value for key, value in static_result.items() if key != "text"
    }
    rendered_payload["render_fetch"] = {
        key: value for key, value in rendered_result.items() if key != "text"
    }
    if rendered_payload["events_count"] == 0:
        rendered_payload["status"] = "render_failed" if rendered_result.get("status") != "ok" else "no_public_odds_found"
        if rendered_result.get("error"):
            rendered_payload["warnings"].append(str(rendered_result["error"]))
    return rendered_payload


async def build_betmgm_public_validation_report(
    db: Session,
    *,
    fixture_limit: int = 15,
    now: datetime | None = None,
) -> dict[str, Any]:
    now_utc = _aware_utc(now or datetime.now(timezone.utc))
    probe = await probe_betmgm_public_odds(use_cds=True, fixture_limit=fixture_limit)
    events = probe.get("events") or []
    rows = [_validate_public_event(db, event, now_utc=now_utc) for event in events]
    counts = Counter(row["validation_status"] for row in rows)
    return {
        "status": "ok" if probe.get("status") == "ok" else "source_warning",
        "source_status": probe.get("status"),
        "provider": BETMGM_PUBLIC_SPORTSBOOK,
        "validated_at": now_utc.isoformat(),
        "events_checked": len(rows),
        "counts": dict(counts),
        "match": [row for row in rows if row["validation_status"] == "MATCH"],
        "mismatch": [row for row in rows if row["validation_status"] == "MISMATCH"],
        "rejected": [row for row in rows if row["validation_status"] == "REJECTED"],
        "source_summary": {
            "source_mode": probe.get("source_mode"),
            "events_count": probe.get("events_count"),
            "warnings": probe.get("warnings") or [],
            "public_cds": probe.get("public_cds"),
        },
        "guardrail": (
            "Validation only: rows must be pre-start, main-market shaped, and close to trusted stored odds "
            "before betmgm_public can be considered as a fallback."
        ),
    }


async def _probe_public_cds_odds(*, fixture_limit: int, timeout_seconds: float) -> dict[str, Any]:
    warnings: list[str] = []
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        config = await _fetch_public_cds_config(client)
        if config.get("status") != "ok":
            warnings.append(str(config.get("error") or "Unable to load public BetMGM client config."))
        access_id = config.get("public_access_id") or BETMGM_PUBLIC_ACCESS_ID_FALLBACK
        cds_api_url = (config.get("cds_api_url") or BETMGM_CDS_API_URL_FALLBACK).rstrip("/")
        fixtures_result = await _fetch_public_mlb_fixture_list(
            client,
            cds_api_url=cds_api_url,
            access_id=access_id,
        )
        if fixtures_result.get("status") != "ok":
            return _cds_payload(
                status="cds_error",
                events=[],
                config=config,
                fixtures_result=fixtures_result,
                warnings=warnings + [str(fixtures_result.get("error") or "Unable to load public MLB fixtures.")],
            )

        fixtures = [
            fixture for fixture in fixtures_result.get("fixtures", [])
            if _is_matchup_fixture(fixture)
        ]
        if not fixtures:
            return _cds_payload(
                status="cds_no_fixtures",
                events=[],
                config=config,
                fixtures_result=fixtures_result,
                warnings=warnings + ["Public CDS returned no MLB matchup fixtures."],
            )

        events: list[dict[str, Any]] = []
        fixture_errors: list[dict[str, Any]] = []
        for fixture in fixtures[:fixture_limit]:
            view = await _fetch_public_fixture_view(
                client,
                cds_api_url=cds_api_url,
                access_id=access_id,
                fixture_id=str(fixture.get("id")),
            )
            if view.get("status") != "ok":
                fixture_errors.append({
                    "fixture_id": fixture.get("id"),
                    "game": _value_name(fixture),
                    "error": view.get("error"),
                    "http_status": view.get("http_status"),
                })
                continue
            event = _parse_fixture_view_odds(view.get("fixture") or fixture)
            if event.get("has_any_market"):
                events.append(event)
            else:
                fixture_errors.append({
                    "fixture_id": fixture.get("id"),
                    "game": _value_name(fixture),
                    "error": "No complete moneyline or totals markets in fixture-view.",
                })

    if fixture_errors:
        warnings.append(f"{len(fixture_errors)} fixture views did not produce usable odds.")

    return _cds_payload(
        status="ok" if events else "cds_no_public_odds_found",
        events=events,
        config=config,
        fixtures_result={
            key: value for key, value in fixtures_result.items()
            if key != "fixtures"
        } | {
            "matchup_fixtures": len(fixtures),
            "fixture_limit": fixture_limit,
            "fixture_errors": fixture_errors[:10],
        },
        warnings=warnings,
    )


async def _fetch_public_cds_config(client: httpx.AsyncClient) -> dict[str, Any]:
    headers = {"User-Agent": _PUBLIC_USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    try:
        page = await client.get(BETMGM_PUBLIC_MLB_URL, headers=headers)
        final_url = str(page.url)
        parts = urlsplit(final_url)
        if not parts.netloc:
            raise RuntimeError("BetMGM page did not return a usable final host.")
        browser_url = final_url.replace("https://", "http://", 1)
        encoded_browser_url = quote(browser_url, safe="")
        config_url = f"https://{parts.netloc}/en/api/clientconfig"
        config_headers = {
            "User-Agent": _PUBLIC_USER_AGENT,
            "Accept": "application/json",
            "x-bwin-browser-url": encoded_browser_url,
            "x-from-product": "host-app",
            "x-bwin-sports-api": "prod",
        }
        response = await client.get(
            config_url,
            params={"browserUrl": browser_url, "x-from-product": "host-app"},
            headers=config_headers,
        )
        response.raise_for_status()
        payload = response.json()
        connection = payload.get("msConnection") or {}
        sports_version = payload.get("msSportsApiVersion") or {}
        return {
            "status": "ok",
            "final_page_url": final_url,
            "config_url": str(response.url),
            "public_access_id": connection.get("publicAccessId"),
            "cds_api_url": connection.get("cdsApiUrl"),
            "sports_api_version": sports_version.get("sportsApiVersion"),
            "sports_api_version_header": sports_version.get("sportsApiVersionHeaderName"),
        }
    except Exception as exc:
        return {"status": "error", "error": _compact_error(exc)}


async def _fetch_public_mlb_fixture_list(
    client: httpx.AsyncClient,
    *,
    cds_api_url: str,
    access_id: str,
) -> dict[str, Any]:
    url = f"{cds_api_url}/bettingoffer/fixtures"
    try:
        response = await client.get(url, params=_cds_params(access_id), headers=_cds_headers())
        response.raise_for_status()
        payload = response.json()
        fixtures = payload.get("fixtures") or []
        return {
            "status": "ok",
            "url": str(response.url),
            "http_status": response.status_code,
            "total_count": payload.get("totalCount"),
            "fixtures_count": len(fixtures),
            "fixtures": fixtures,
        }
    except Exception as exc:
        return {"status": "error", "url": url, "error": _compact_error(exc)}


async def _fetch_public_fixture_view(
    client: httpx.AsyncClient,
    *,
    cds_api_url: str,
    access_id: str,
    fixture_id: str,
) -> dict[str, Any]:
    url = f"{cds_api_url}/bettingoffer/fixture-view"
    params = _cds_params(access_id) | {
        "fixtureIds": fixture_id,
        "offerMapping": "All",
        "scoreboardMode": "Full",
    }
    try:
        response = await client.get(url, params=params, headers=_cds_headers())
        response.raise_for_status()
        payload = response.json()
        return {
            "status": "ok",
            "url": str(response.url),
            "http_status": response.status_code,
            "fixture": payload.get("fixture") or {},
        }
    except Exception as exc:
        return {"status": "error", "url": url, "error": _compact_error(exc)}


def _cds_params(access_id: str) -> dict[str, Any]:
    return {
        "x-bwin-accessid": access_id,
        "lang": "en-us",
        "country": "US",
        "usercountry": "US",
        "state": "Latest",
        "sportIds": BETMGM_BASEBALL_SPORT_ID,
        "competitionIds": BETMGM_MLB_COMPETITION_ID,
    }


def _cds_headers() -> dict[str, str]:
    return {
        "User-Agent": _PUBLIC_USER_AGENT,
        "Accept": "application/json",
        "Origin": "https://www.il.betmgm.com",
        "Referer": BETMGM_PUBLIC_MLB_URL,
        "Sports-Api-Version": "SportsAPIv2",
    }


def _cds_payload(
    *,
    status: str,
    events: list[dict[str, Any]],
    config: dict[str, Any],
    fixtures_result: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "status": status,
        "provider": BETMGM_PUBLIC_SPORTSBOOK,
        "source_mode": "public_cds",
        "source_url": BETMGM_PUBLIC_MLB_URL,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "events_count": len(events),
        "events": events,
        "warnings": warnings[:25],
        "public_cds": {
            "config": config,
            "fixtures": fixtures_result,
        },
    }


def _validate_public_event(db: Session, event: dict[str, Any], *, now_utc: datetime) -> dict[str, Any]:
    reasons: list[str] = []
    game = _match_db_game(db, event)
    trusted = _latest_trusted_odds(db, game.game_id) if game else None
    start_utc = _event_start_utc(event, game)
    if game is None:
        reasons.append("no_matching_db_game")
    if start_utc is None:
        reasons.append("missing_start_time")
    elif start_utc <= now_utc:
        reasons.append("fixture_already_started")
    if not event.get("complete_h2h"):
        reasons.append("missing_moneyline")
    if not event.get("complete_total"):
        reasons.append("missing_total")
    reasons.extend(_event_total_reject_reasons(event))
    if trusted is None:
        reasons.append("missing_trusted_odds_snapshot")

    comparison = _compare_to_trusted(event, trusted) if trusted is not None else {"matches": False, "diffs": {}}
    status = "REJECTED"
    if not reasons:
        status = "MATCH" if comparison.get("matches") else "MISMATCH"

    return {
        "validation_status": status,
        "reasons": reasons,
        "game_id": game.game_id if game else None,
        "matchup": f"{game.away_team} @ {game.home_team}" if game else event.get("event_label"),
        "start_time": game.start_time if game else event.get("commence_time"),
        "minutes_to_start": (
            round((start_utc - now_utc).total_seconds() / 60, 1)
            if start_utc is not None else None
        ),
        "betmgm_public": _event_odds_summary(event),
        "trusted_odds": _trusted_odds_summary(trusted),
        "comparison": comparison,
        "total_candidates": event.get("total_candidates") or [],
        "total_rejected_candidates": event.get("total_rejected_candidates") or [],
    }


def _match_db_game(db: Session, event: dict[str, Any]) -> Game | None:
    away = _normalize_name(event.get("away_team_hint"))
    home = _normalize_name(event.get("home_team_hint"))
    start = _parse_datetime(event.get("commence_time"))
    query = db.query(Game)
    if start is not None:
        query = query.filter(Game.game_date.in_([start.date(), start.astimezone(timezone.utc).date()]))
    games = query.all()
    for game in games:
        if _normalize_name(game.away_team) == away and _normalize_name(game.home_team) == home:
            return game
    return None


def _latest_trusted_odds(db: Session, game_id: int) -> GameOdds | None:
    return (
        db.query(GameOdds)
        .filter(
            GameOdds.game_id == game_id,
            GameOdds.sportsbook != BETMGM_PUBLIC_SPORTSBOOK,
            GameOdds.snapshot_type.in_([SnapshotType.pregame, SnapshotType.open]),
        )
        .order_by(
            GameOdds.snapshot_type.desc(),
            GameOdds.fetched_at.desc(),
            GameOdds.id.desc(),
        )
        .first()
    )


def _event_start_utc(event: dict[str, Any], game: Game | None) -> datetime | None:
    return _parse_datetime(game.start_time if game else None) or _parse_datetime(event.get("commence_time"))


def _event_total_reject_reasons(event: dict[str, Any]) -> list[str]:
    reasons = []
    candidate = {
        "line": event.get("total_line"),
        "over_odds": event.get("over_odds"),
        "under_odds": event.get("under_odds"),
    }
    for reason in _total_candidate_reject_reasons(candidate):
        reasons.append(f"selected_{reason}")
    return reasons


def _compare_to_trusted(event: dict[str, Any], trusted: GameOdds | None) -> dict[str, Any]:
    if trusted is None:
        return {"matches": False, "diffs": {}}
    trusted_total = float(trusted.total_line) if trusted.total_line is not None else None
    diffs = {
        "away_ml": _int_diff(event.get("away_ml"), trusted.away_ml),
        "home_ml": _int_diff(event.get("home_ml"), trusted.home_ml),
        "total_line": _float_diff(event.get("total_line"), trusted_total),
        "over_odds": _int_diff(event.get("over_odds"), trusted.over_odds),
        "under_odds": _int_diff(event.get("under_odds"), trusted.under_odds),
    }
    checks = {
        "away_ml": _abs_le(diffs["away_ml"], MONEYLINE_MATCH_TOLERANCE),
        "home_ml": _abs_le(diffs["home_ml"], MONEYLINE_MATCH_TOLERANCE),
        "total_line": _abs_le(diffs["total_line"], TOTAL_MATCH_TOLERANCE),
        "over_odds": _abs_le(diffs["over_odds"], TOTAL_PRICE_MATCH_TOLERANCE),
        "under_odds": _abs_le(diffs["under_odds"], TOTAL_PRICE_MATCH_TOLERANCE),
    }
    return {
        "matches": all(checks.values()),
        "checks": checks,
        "diffs": diffs,
        "tolerances": {
            "moneyline": MONEYLINE_MATCH_TOLERANCE,
            "total_line": TOTAL_MATCH_TOLERANCE,
            "total_price": TOTAL_PRICE_MATCH_TOLERANCE,
        },
    }


def _event_odds_summary(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "fixture_id": event.get("fixture_id"),
        "away_team": event.get("away_team_hint"),
        "home_team": event.get("home_team_hint"),
        "away_ml": event.get("away_ml"),
        "home_ml": event.get("home_ml"),
        "total_line": event.get("total_line"),
        "over_odds": event.get("over_odds"),
        "under_odds": event.get("under_odds"),
        "selected_total_candidate": event.get("selected_total_candidate"),
    }


def _trusted_odds_summary(odds: GameOdds | None) -> dict[str, Any] | None:
    if odds is None:
        return None
    return {
        "id": odds.id,
        "sportsbook": odds.sportsbook,
        "snapshot_type": odds.snapshot_type.value if odds.snapshot_type else None,
        "fetched_at": odds.fetched_at.isoformat() if odds.fetched_at else None,
        "away_ml": odds.away_ml,
        "home_ml": odds.home_ml,
        "total_line": float(odds.total_line) if odds.total_line is not None else None,
        "over_odds": odds.over_odds,
        "under_odds": odds.under_odds,
    }


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware_utc(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware_utc(parsed)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _int_diff(left: Any, right: Any) -> int | None:
    if left is None or right is None:
        return None
    return int(left) - int(right)


def _float_diff(left: Any, right: Any) -> float | None:
    if left is None or right is None:
        return None
    return round(float(left) - float(right), 3)


def _abs_le(value: int | float | None, threshold: int | float) -> bool:
    return value is not None and abs(value) <= threshold


def _is_matchup_fixture(fixture: dict[str, Any]) -> bool:
    participants = fixture.get("participants") or []
    name = _value_name(fixture)
    return (
        len(participants) >= 2
        and " at " in name.lower()
        and (fixture.get("sport") or {}).get("id") == BETMGM_BASEBALL_SPORT_ID
        and ((fixture.get("competition") or {}).get("id") == BETMGM_MLB_COMPETITION_ID)
    )


def _parse_fixture_view_odds(fixture: dict[str, Any]) -> dict[str, Any]:
    away_team, home_team = _split_matchup(_value_name(fixture))
    event: dict[str, Any] = {
        "sportsbook": BETMGM_PUBLIC_SPORTSBOOK,
        "fixture_id": str(fixture.get("id")) if fixture.get("id") is not None else None,
        "away_team_hint": away_team,
        "home_team_hint": home_team,
        "event_label": _value_name(fixture),
        "commence_time": fixture.get("startDate"),
        "source_url": BETMGM_PUBLIC_MLB_URL,
        "market_format": "public_cds",
        "away_ml": None,
        "home_ml": None,
        "total_line": None,
        "over_odds": None,
        "under_odds": None,
        "total_candidates": [],
        "total_rejected_candidates": [],
        "spread_rows": [],
    }

    for market in fixture.get("optionMarkets") or []:
        market_name = _value_name(market).lower()
        if market_name == "moneyline":
            _parse_cds_moneyline(event, market)
        elif market_name == "totals":
            _parse_cds_totals(event, market)
        elif "run line" in market_name:
            _parse_cds_spread(event, market)

    _select_main_total(event)
    event["has_any_market"] = any(
        event.get(key) is not None
        for key in ("away_ml", "home_ml", "total_line", "over_odds", "under_odds")
    )
    event["complete_h2h"] = event.get("away_ml") is not None and event.get("home_ml") is not None
    event["complete_total"] = (
        event.get("total_line") is not None
        and event.get("over_odds") is not None
        and event.get("under_odds") is not None
    )
    return event


def _parse_cds_moneyline(event: dict[str, Any], market: dict[str, Any]) -> None:
    for option in market.get("options") or []:
        source = _nested_value(option, "sourceName").strip()
        american = _cds_american_odds(option)
        if source == "1":
            event["away_ml"] = american
        elif source == "2":
            event["home_ml"] = american


def _parse_cds_totals(event: dict[str, Any], market: dict[str, Any]) -> None:
    candidate: dict[str, Any] = {
        "line": None,
        "over_odds": None,
        "under_odds": None,
        "market_id": market.get("id"),
    }
    for option in market.get("options") or []:
        name = _value_name(option)
        american = _cds_american_odds(option)
        line = _line_from_option_name(name)
        if line is not None:
            candidate["line"] = line
        if name.lower().startswith("over"):
            candidate["over_odds"] = american
        elif name.lower().startswith("under"):
            candidate["under_odds"] = american
    if candidate["line"] is not None and (candidate["over_odds"] is not None or candidate["under_odds"] is not None):
        event.setdefault("total_candidates", []).append(candidate)


def _select_main_total(event: dict[str, Any]) -> None:
    candidates = event.get("total_candidates") or []
    viable = [
        candidate for candidate in candidates
        if not _total_candidate_reject_reasons(candidate)
    ]
    selected = min(viable, key=_main_total_score) if viable else None
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        reasons = _total_candidate_reject_reasons(candidate)
        if selected is candidate:
            continue
        rejected.append(candidate | {"reject_reasons": reasons or ["alternate_total_after_main_selection"]})

    if selected is None and candidates:
        selected = candidates[0]
        selected = selected | {"selected_with_warnings": _total_candidate_reject_reasons(selected)}
        rejected = [
            candidate | {"reject_reasons": _total_candidate_reject_reasons(candidate) or ["not_first_total_candidate"]}
            for candidate in candidates[1:]
        ]

    if selected:
        event["total_line"] = selected.get("line")
        event["over_odds"] = selected.get("over_odds")
        event["under_odds"] = selected.get("under_odds")
        event["selected_total_candidate"] = selected
    event["total_rejected_candidates"] = rejected


def _main_total_score(candidate: dict[str, Any]) -> float:
    over = candidate.get("over_odds")
    under = candidate.get("under_odds")
    if over is None or under is None:
        return 9999
    # Main totals tend to have the most balanced prices around even/-110.
    return abs(abs(int(over)) - 110) + abs(abs(int(under)) - 110)


def _total_candidate_reject_reasons(candidate: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    line = _to_float(candidate.get("line"))
    over_odds = candidate.get("over_odds")
    under_odds = candidate.get("under_odds")
    if line is None:
        reasons.append("missing_total_line")
    elif line < MLB_TOTAL_MIN or line > MLB_TOTAL_MAX:
        reasons.append("suspicious_total_line")
    if over_odds is None or under_odds is None:
        reasons.append("missing_total_side")
    if (
        (isinstance(over_odds, int) and abs(over_odds) > MAX_MAIN_TOTAL_PRICE_ABS)
        or (isinstance(under_odds, int) and abs(under_odds) > MAX_MAIN_TOTAL_PRICE_ABS)
    ):
        reasons.append("suspicious_total_price")
    return reasons


def _parse_cds_spread(event: dict[str, Any], market: dict[str, Any]) -> None:
    spreads = event.setdefault("spread_rows", [])
    for option in market.get("options") or []:
        name = _value_name(option)
        line = _line_from_option_name(name)
        if line is None:
            continue
        spreads.append({
            "name": name,
            "point": line,
            "american_odds": _cds_american_odds(option),
        })


def _cds_american_odds(option: dict[str, Any]) -> int | None:
    price = option.get("price") or {}
    american = price.get("americanOdds")
    if american is not None:
        try:
            return int(american)
        except (TypeError, ValueError):
            pass
    return decimal_to_american(_to_float(price.get("odds")))


def _line_from_option_name(name: str) -> float | None:
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*$", name or "")
    return _to_float(match.group(1)) if match else None


def _value_name(row: dict[str, Any]) -> str:
    return _nested_value(row, "name")


def _nested_value(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if isinstance(value, dict):
        return str(value.get("value") or "")
    return str(value or "")


def _split_matchup(name: str) -> tuple[str | None, str | None]:
    parts = re.split(r"\s+at\s+", name or "", maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None, None
    return parts[0].strip(), parts[1].strip()


async def _fetch_static_page_text(*, timeout_seconds: float) -> dict[str, Any]:
    headers = {"User-Agent": _PUBLIC_USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_seconds) as client:
            response = await client.get(BETMGM_PUBLIC_MLB_URL, headers=headers)
        return {
            "status": "ok",
            "http_status": response.status_code,
            "final_url": str(response.url),
            "content_type": response.headers.get("content-type"),
            "bytes": len(response.content or b""),
            "text": response.text,
        }
    except httpx.HTTPError as exc:
        return {"status": "error", "error": _compact_error(exc), "text": ""}


async def _fetch_rendered_page_text(*, timeout_seconds: float) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "status": "unavailable",
            "error": "Playwright is not installed in this environment; static probe only.",
            "text": "",
        }

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=_PUBLIC_USER_AGENT,
                locale="en-US",
                timezone_id="America/Chicago",
            )
            page = await context.new_page()
            try:
                await page.goto(BETMGM_PUBLIC_MLB_URL, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
                await page.wait_for_timeout(7000)
                body_text = await page.locator("body").inner_text(timeout=int(timeout_seconds * 1000))
                return {
                    "status": "ok",
                    "final_url": page.url,
                    "bytes": len(body_text.encode("utf-8")),
                    "text": body_text,
                }
            finally:
                await browser.close()
    except Exception as exc:
        return {"status": "error", "error": _compact_error(exc), "text": ""}


def _teams_in_line(line: str) -> list[str]:
    matches = []
    used: set[str] = set()
    for match in _TEAM_PATTERN.finditer(line or ""):
        team = _TEAM_ALIASES.get(match.group(1).lower(), match.group(1))
        if team in used:
            continue
        used.add(team)
        matches.append((match.start(), team))
    return [team for _pos, team in sorted(matches)]


def _looks_like_game_row(lines: list[str], idx: int) -> bool:
    nearby_after = lines[idx + 1 : idx + 18]
    nearby_before = lines[max(0, idx - 8) : idx]
    has_wagers_marker = any(line.lower() == "all wagers" for line in nearby_after)
    has_market_header = any(line.lower() in {"spread", "total", "money"} for line in nearby_before + nearby_after)
    return has_wagers_marker and has_market_header


def _event_window(lines: list[str], idx: int) -> list[str]:
    window: list[str] = []
    for line in lines[idx + 1 : idx + 18]:
        if line.lower() == "all wagers":
            break
        window.append(line)
    return window


def _parse_market_window(lines: list[str]) -> dict[str, Any]:
    spreads: list[dict[str, Any]] = []
    total_line = None
    over_odds = None
    under_odds = None
    money_decimals: list[float] = []
    saw_total = False

    for line in lines:
        if spread_match := _SPREAD_RE.match(line):
            decimal = _to_float(spread_match.group("decimal"))
            spreads.append({
                "point": _to_float(spread_match.group("point")),
                "decimal_odds": decimal,
                "american_odds": decimal_to_american(decimal),
            })
            continue

        if total_match := _TOTAL_RE.match(line):
            saw_total = True
            total_line = _to_float(total_match.group("line"))
            decimal = _to_float(total_match.group("decimal"))
            if total_match.group("side").upper() == "O":
                over_odds = decimal_to_american(decimal)
            else:
                under_odds = decimal_to_american(decimal)
            continue

        if saw_total and _DECIMAL_RE.match(line):
            decimal = _to_float(line)
            if decimal is not None and 1.01 <= decimal <= 20:
                money_decimals.append(decimal)

    away_ml = decimal_to_american(money_decimals[0]) if len(money_decimals) >= 1 else None
    home_ml = decimal_to_american(money_decimals[1]) if len(money_decimals) >= 2 else None
    return {
        "away_ml": away_ml,
        "home_ml": home_ml,
        "total_line": total_line,
        "over_odds": over_odds,
        "under_odds": under_odds,
        "spread_rows": spreads[:2],
        "has_any_market": any(value is not None for value in (away_ml, home_ml, total_line, over_odds, under_odds)),
        "complete_h2h": away_ml is not None and home_ml is not None,
        "complete_total": total_line is not None and over_odds is not None and under_odds is not None,
    }


def _to_float(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compact_error(exc: Exception) -> str:
    message = str(exc).replace("\n", " ").strip()
    return message[:500] or exc.__class__.__name__
