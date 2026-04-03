from __future__ import annotations

from app.config import ALERT_DESTINATION, DISCORD_WEBHOOK_URL

if not DISCORD_WEBHOOK_URL:
    print("[alerts] WARNING: DISCORD_WEBHOOK_URL is not set", flush=True)


def send_alert_message(message: str) -> tuple[bool, str | None]:
    if ALERT_DESTINATION == "discord" and DISCORD_WEBHOOK_URL:
        import requests

        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=15)
        print(f"[alerts] Discord webhook status: {resp.status_code}", flush=True)
        if resp.status_code == 204 or 200 <= resp.status_code < 300:
            return True, None
        print(f"[alerts] Discord webhook error body: {resp.text[:500]}", flush=True)
        return False, f"discord webhook returned {resp.status_code}: {resp.text[:500]}"

    # no destination configured yet -> do not treat as failure
    return True, "notification skipped: no configured alert destination yet"
