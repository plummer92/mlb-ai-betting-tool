from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx

BETMGM_PUBLIC_MLB_URL = "https://www.betmgm.com/en/sports/baseball-23/betting/usa-9/mlb-75"
BETMGM_PUBLIC_SPORTSBOOK = "betmgm_public"

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


async def probe_betmgm_public_odds(*, render: bool = False, timeout_seconds: float = 18.0) -> dict[str, Any]:
    static_result = await _fetch_static_page_text(timeout_seconds=timeout_seconds)
    static_payload = parse_betmgm_public_page_text(static_result.get("text") or "")
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
