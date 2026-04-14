"""
v0.4 Sandbox Discord alerts.

Sends purple-themed embeds to DISCORD_SANDBOX_WEBHOOK_URL.
Never raises — all errors are swallowed silently.
"""

from __future__ import annotations

import os
from typing import Optional

import requests
from sqlalchemy.orm import Session

SANDBOX_WEBHOOK_URL: str = os.getenv("DISCORD_SANDBOX_WEBHOOK_URL", "")
PURPLE_COLOR = 9442302  # 0x9013FE


def send_sandbox_alert(v4_pred: dict, game, db: Session) -> None:
    """
    Post a v0.4 sandbox embed to Discord.

    Only sends if DISCORD_SANDBOX_WEBHOOK_URL is set and non-empty.
    Never raises on HTTP error or missing data.
    """
    try:
        webhook_url = os.getenv("DISCORD_SANDBOX_WEBHOOK_URL", "").strip()
        if not webhook_url:
            return

        away = v4_pred.get("away_team", "AWAY")
        home = v4_pred.get("home_team", "HOME")
        f5_pick = v4_pred.get("f5_pick") or "N/A"
        f5_line = v4_pred.get("f5_line")
        f5_proj = v4_pred.get("f5_projection", 0.0) or 0.0
        v4_total = v4_pred.get("v4_total", 0.0) or 0.0
        v3_total = v4_pred.get("v3_total", 0.0) or 0.0
        umpire_name = v4_pred.get("umpire_name", "Unknown")
        umpire_impact = v4_pred.get("umpire_run_impact", 0.0) or 0.0
        away_bp = v4_pred.get("away_bullpen_strength", 1.0) or 1.0
        home_bp = v4_pred.get("home_bullpen_strength", 1.0) or 1.0
        agreement = v4_pred.get("v3_v4_agreement", False)
        delta = v4_total - v3_total

        # Full-game pick based on v4 vs v3 spread
        if v4_total > v3_total + 0.3:
            full_game_pick = "OVER"
        elif v4_total < v3_total - 0.3:
            full_game_pick = "UNDER"
        else:
            full_game_pick = "PUSH"

        f5_line_display = f"{f5_line:.1f}" if f5_line is not None else "N/A"

        confidence_badge = (
            "\u2705 HIGH CONFIDENCE" if agreement else "\u26a0\ufe0f Models Diverge"
        )

        description = (
            f"**F5 Pick:** F5 {f5_pick} {f5_line_display} (proj: {f5_proj:.1f})\n"
            f"**Full Game:** {full_game_pick} (proj: {v4_total:.1f})\n"
            f"**Umpire:** {umpire_name} ({umpire_impact:+.2f} runs/gm)\n"
            f"**Bullpen:** {away} {away_bp:.0%} vs {home} {home_bp:.0%}\n"
            f"**v3 proj:** {v3_total:.1f} | **v4 proj:** {v4_total:.1f} "
            f"(delta: {delta:+.1f})\n"
            f"{confidence_badge}"
        )

        payload = {
            "embeds": [
                {
                    "title": f"\U0001f9ea v0.4 SANDBOX | {away} @ {home}",
                    "description": description,
                    "color": PURPLE_COLOR,
                }
            ]
        }

        resp = requests.post(webhook_url, json=payload, timeout=10)
        if not resp.ok:
            print(f"[v4 alert] Discord HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"[v4 alert] send_sandbox_alert silenced error: {e}")
