import os

import anthropic

_SYSTEM = (
    "You are a sharp MLB betting analyst. Write 2-3 sentences explaining why this pick has edge. "
    "Be specific about the stats. Use plain text, no markdown, no emojis. Be confident but concise."
)


def generate_pick_explanation(game_data: dict) -> str:
    try:
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        away = game_data.get("away_team", "")
        home = game_data.get("home_team", "")
        play = game_data.get("recommended_play", "")
        edge_pct = float(game_data.get("edge_pct") or 0)
        ev = float(game_data.get("ev") or 0)

        away_starter = game_data.get("away_starter") or "Unknown"
        home_starter = game_data.get("home_starter") or "Unknown"
        away_xera = game_data.get("away_starter_xera")
        home_xera = game_data.get("home_starter_xera")

        wind_factor = game_data.get("wind_factor")
        temp_f = game_data.get("temp_f")
        travel_stress_away = game_data.get("travel_stress_away")
        travel_stress_home = game_data.get("travel_stress_home")
        home_bullpen = game_data.get("home_bullpen_strength")
        away_bullpen = game_data.get("away_bullpen_strength")

        away_era_str = f" (xERA: {float(away_xera):.2f})" if away_xera is not None else ""
        home_era_str = f" (xERA: {float(home_xera):.2f})" if home_xera is not None else ""

        lines = [
            f"Game: {away} @ {home}",
            f"Pick: {play}",
            f"Edge: {edge_pct:.1%} | EV: {ev:.1%}",
            f"Away starter: {away_starter}{away_era_str}",
            f"Home starter: {home_starter}{home_era_str}",
        ]

        if wind_factor is not None:
            lines.append(f"Wind factor: {float(wind_factor):.2f}")
        if temp_f is not None:
            lines.append(f"Temp: {float(temp_f):.0f}F")
        if travel_stress_away is not None:
            lines.append(f"Away travel stress: {float(travel_stress_away):.2f}")
        if travel_stress_home is not None:
            lines.append(f"Home travel stress: {float(travel_stress_home):.2f}")
        if home_bullpen is not None:
            lines.append(f"Home bullpen strength: {float(home_bullpen):.2f}")
        if away_bullpen is not None:
            lines.append(f"Away bullpen strength: {float(away_bullpen):.2f}")

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=150,
            system=_SYSTEM,
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )

        explanation = response.content[0].text.strip()
        print(f"[ai explain] generated for game_id={game_data.get('game_id', '?')}", flush=True)
        return explanation
    except Exception:
        return ""
