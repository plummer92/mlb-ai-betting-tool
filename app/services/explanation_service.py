def generate_pick_explanation(game_data: dict) -> str:
    if not game_data:
        return ""

    play = game_data.get("recommended_play", "")
    away = game_data.get("away_team", "")
    home = game_data.get("home_team", "")
    edge_pct = game_data.get("edge_pct")
    ev = game_data.get("ev")

    if not play or edge_pct is None or ev is None:
        return ""

    edge_pct = float(edge_pct)
    ev = float(ev)

    sentences = []
    is_totals = play in ("over", "under")

    away_starter = game_data.get("away_starter") or ""
    home_starter = game_data.get("home_starter") or ""
    away_xera = game_data.get("away_starter_xera")
    home_xera = game_data.get("home_starter_xera")
    wind_factor = game_data.get("wind_factor")
    travel_stress_away = game_data.get("travel_stress_away")
    home_bullpen = game_data.get("home_bullpen_strength")
    away_bullpen = game_data.get("away_bullpen_strength")
    park = game_data.get("park") or "this park"

    if not is_totals:
        if play == "away_ml":
            starter_name = away_starter
            starter_xera = away_xera
            team = away
            opponent_starter = home_starter
        else:
            starter_name = home_starter
            starter_xera = home_xera
            team = home
            opponent_starter = away_starter

        if starter_name:
            if starter_xera is not None:
                sentences.append(
                    f"{starter_name} ({float(starter_xera):.2f} xERA) gives {team} a clear pitching edge."
                )
            elif opponent_starter:
                sentences.append(
                    f"{starter_name} is projected to outperform {opponent_starter}."
                )

    if wind_factor is not None:
        wf = float(wind_factor)
        if wf < -0.3:
            sentences.append(
                f"Wind blowing IN at {park} ({wf:.2f} factor) suppresses fly ball carry, favoring the under."
            )
        elif wf > 0.3:
            sentences.append(
                f"Wind blowing OUT ({wf:.2f} factor) inflates run environment, favoring the over."
            )

    if travel_stress_away is not None and float(travel_stress_away) > 0.25:
        sentences.append(
            f"The {away} carry a {float(travel_stress_away):.0%} travel stress penalty after crossing time zones."
        )

    bullpen_line = None
    if not is_totals:
        if play == "away_ml" and away_bullpen is not None and float(away_bullpen) < 0.4:
            bullpen_line = f"{away} bullpen is at {float(away_bullpen):.0%} availability — late inning vulnerability increases."
        elif play == "home_ml" and home_bullpen is not None and float(home_bullpen) < 0.4:
            bullpen_line = f"{home} bullpen is at {float(home_bullpen):.0%} availability — late inning vulnerability increases."
    else:
        if home_bullpen is not None and float(home_bullpen) < 0.4:
            bullpen_line = f"{home} bullpen is at {float(home_bullpen):.0%} availability — late inning vulnerability increases."
        elif away_bullpen is not None and float(away_bullpen) < 0.4:
            bullpen_line = f"{away} bullpen is at {float(away_bullpen):.0%} availability — late inning vulnerability increases."

    if bullpen_line:
        sentences.append(bullpen_line)

    edge_line = f"Model projects {edge_pct:.1%} edge with {ev:.1%} expected value."

    body = sentences[:2]
    body.append(edge_line)

    return " ".join(body)
