from __future__ import annotations

from app.config import ALERT_DESTINATION, DISCORD_WEBHOOK_URL


def send_alert_message(message: str) -> tuple[bool, str | None]:
    if ALERT_DESTINATION == "discord" and DISCORD_WEBHOOK_URL:
        import requests

        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=15)
        if 200 <= resp.status_code < 300:
            return True, None
        return False, f"discord webhook returned {resp.status_code}: {resp.text[:500]}"

    # no destination configured yet -> do not treat as failure
    return True, "notification skipped: no configured alert destination yet"
