from __future__ import annotations

import argparse
import asyncio
import json

from app.services.betmgm_public_odds_service import probe_betmgm_public_odds


def main() -> None:
    parser = argparse.ArgumentParser(description="Scout BetMGM public MLB odds without logging in or storing odds.")
    parser.add_argument("--render", action="store_true", help="Try a Playwright-rendered page if static HTML has no odds.")
    parser.add_argument("--limit", type=int, default=10, help="Number of parsed event rows to print.")
    args = parser.parse_args()

    payload = asyncio.run(probe_betmgm_public_odds(render=args.render))
    events = payload.get("events") or []
    payload["events"] = events[: max(args.limit, 0)]
    payload["total_events_before_limit"] = len(events)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
