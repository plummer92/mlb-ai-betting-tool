"""
v0.4 Sandbox simulator.

Runs in parallel shadow mode only — never raises, never affects v3 pipeline.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models.schema import Game, Prediction, SandboxPredictionV4
from app.services.bullpen_calc import (
    collect_reliever_workload,
    get_team_bullpen_availability,
)
from app.services.series_service import get_series_opener_edge, get_series_position
from app.services.travel_service import calculate_travel_stress
from app.services.umpire_service import collect_umpire_for_game
from app.services.weather_service import fetch_park_weather


def calculate_f5_projection(
    v3_total: float,
    umpire_impact: float,
    home_starter_era: Optional[float],
    away_starter_era: Optional[float],
) -> float:
    """
    Project first-5-inning total based on v3 total, starter quality, and umpire impact.
    """
    home_era = home_starter_era if home_starter_era is not None else 4.50
    away_era = away_starter_era if away_starter_era is not None else 4.50
    starter_quality = (home_era + away_era) / 2

    if starter_quality < 3.50:
        f5_multiplier = 0.42
    elif starter_quality < 4.50:
        f5_multiplier = 0.45
    else:
        f5_multiplier = 0.48

    f5_base = v3_total * f5_multiplier
    f5_adjusted = f5_base + (umpire_impact * 0.45)
    return round(f5_adjusted, 2)


def calculate_late_inning_projection(
    v3_total: float,
    home_bullpen: float,
    away_bullpen: float,
    umpire_impact: float,
) -> float:
    """
    Project late-inning (innings 6–9) run contribution.
    """
    bullpen_factor = (home_bullpen + away_bullpen) / 2
    bullpen_adjustment = (bullpen_factor - 0.5) * -0.8
    late_base = v3_total * 0.55
    late_adjusted = late_base + bullpen_adjustment + (umpire_impact * 0.55)
    return round(max(0.5, late_adjusted), 2)


def run_v4_sandbox(game_id: int, db: Session) -> Optional[dict]:
    """
    Run the full v0.4 sandbox prediction for a game and upsert into
    sandbox_predictions_v4. Returns result dict or None on any exception.
    NEVER raises.
    """
    try:
        # ── 1. Pull v3 prediction ─────────────────────────────────────────
        pred = (
            db.query(Prediction)
            .filter(Prediction.game_id == game_id, Prediction.is_active == True)  # noqa: E712
            .order_by(Prediction.created_at.desc())
            .first()
        )
        if pred is None:
            return None

        v3_total = float(pred.projected_total)
        v3_home_win_pct = float(pred.home_win_pct)
        home_starter_xera = pred.home_starter_xera
        away_starter_xera = pred.away_starter_xera

        # ── 2. Pull game info ─────────────────────────────────────────────
        game = db.query(Game).filter(Game.game_id == game_id).first()
        if game is None:
            return None

        game_date = game.game_date
        season = game.season
        home_team_id = game.home_team_id
        away_team_id = game.away_team_id

        # ── 3. Umpire data ────────────────────────────────────────────────
        umpire_data = None
        try:
            umpire_data = collect_umpire_for_game(game_id, season, db)
        except Exception:
            pass
        umpire_name = umpire_data["umpire_name"] if umpire_data else "Unknown"
        umpire_impact = umpire_data["run_impact"] if umpire_data else 0.0

        # ── 4. Bullpen data ───────────────────────────────────────────────
        today = date.today()
        home_bullpen = 1.0
        away_bullpen = 1.0
        try:
            if home_team_id:
                collect_reliever_workload(home_team_id, today, db)
                home_bullpen = get_team_bullpen_availability(home_team_id, today, db)
        except Exception:
            pass
        try:
            if away_team_id:
                collect_reliever_workload(away_team_id, today, db)
                away_bullpen = get_team_bullpen_availability(away_team_id, today, db)
        except Exception:
            pass

        # ── 5. Travel stress ─────────────────────────────────────────────
        home_travel_stress = 0.0
        away_travel_stress = 0.0
        try:
            if home_team_id:
                home_travel_stress = calculate_travel_stress(home_team_id, game_date, db)
        except Exception:
            pass
        try:
            if away_team_id:
                away_travel_stress = calculate_travel_stress(away_team_id, game_date, db)
        except Exception:
            pass

        # ── 6. Weather/wind ───────────────────────────────────────────────
        wind_factor = 0.0
        wind_mph = 0.0
        temp_f: Optional[float] = None
        humidity_pct: Optional[float] = None
        is_dome = False
        try:
            if home_team_id:
                wx = fetch_park_weather(home_team_id, game_date)
                wind_factor = wx["wind_factor"]
                wind_mph = wx["wind_mph"]
                temp_f = wx["temp_f"]
                humidity_pct = wx["humidity_pct"]
                is_dome = wx["is_dome"]
                print(
                    f"[v4 weather] game_id={game_id} "
                    f"wind={wx['wind_mph']:.1f}mph "
                    f"factor={wind_factor:.2f} "
                    f"temp={temp_f}F",
                    flush=True,
                )
        except Exception:
            pass

        # ── 6.5. Series position ─────────────────────────────────────────
        series_game_number = None
        is_series_opener = False
        is_series_finale = False
        series_edge = 0.0
        try:
            if home_team_id:
                home_series = get_series_position(home_team_id, game_date, db)
                series_edge = get_series_opener_edge(home_series)
                series_game_number = home_series.get("series_game_number")
                is_series_opener = home_series.get("is_series_opener", False)
                is_series_finale = home_series.get("is_series_finale", False)
            if away_team_id:
                get_series_position(away_team_id, game_date, db)
            print(
                f"[v4 series] game_id={game_id} "
                f"series_game={series_game_number} "
                f"opener={is_series_opener} "
                f"finale={is_series_finale}",
                flush=True,
            )
        except Exception:
            pass

        # ── 7. Projections ────────────────────────────────────────────────
        f5_projection = calculate_f5_projection(
            v3_total, umpire_impact, home_starter_xera, away_starter_xera
        )
        late_inning_projection = calculate_late_inning_projection(
            v3_total, home_bullpen, away_bullpen, umpire_impact
        )

        # Apply wind factor: max ±6% effect on each segment
        f5_projection = round(f5_projection * (1 + wind_factor * 0.06), 2)
        late_inning_projection = round(
            max(0.5, late_inning_projection * (1 + wind_factor * 0.06)), 2
        )

        v4_total = round(f5_projection + late_inning_projection + series_edge, 2)

        # ── 8. F5 line comparison ─────────────────────────────────────────
        # Look for an F5 line; fall back to v3_total * 0.45 as neutral line
        from app.models.schema import F5LineV4
        f5_line_row = (
            db.query(F5LineV4)
            .filter(F5LineV4.game_id == game_id)
            .order_by(F5LineV4.timestamp.desc())
            .first()
        )
        f5_line = f5_line_row.f5_over_under_line if f5_line_row else round(v3_total * 0.45, 1)

        if f5_line:
            f5_pick = "OVER" if f5_projection > f5_line else "UNDER"
            f5_edge_pct = round(abs(f5_projection - f5_line) / max(f5_line, 0.1), 4)
        else:
            f5_pick = None
            f5_edge_pct = 0.0

        # ── 9. Agreement / convergence flags ─────────────────────────────
        v3_v4_agreement = abs(v4_total - v3_total) < 0.5
        bullpen_convergence = (home_bullpen > 0.7 and away_bullpen > 0.7) or \
                              (home_bullpen < 0.3 and away_bullpen < 0.3)

        # Confidence: agreement + bullpen alignment + umpire certainty
        v4_confidence = 0.0
        if v3_v4_agreement:
            v4_confidence += 0.4
        if bullpen_convergence:
            v4_confidence += 0.3
        if umpire_name != "Unknown":
            v4_confidence += 0.3

        # v4 doesn't change win probability — use v3 as baseline
        v4_home_win_pct = v3_home_win_pct

        # ── 10. Upsert to sandbox_predictions_v4 ─────────────────────────
        existing = (
            db.query(SandboxPredictionV4)
            .filter(SandboxPredictionV4.game_id == game_id)
            .first()
        )
        if existing:
            existing.f5_projected_total = f5_projection
            existing.f5_line = f5_line
            existing.f5_pick = f5_pick
            existing.f5_edge_pct = f5_edge_pct
            existing.umpire_name = umpire_name
            existing.umpire_run_impact = umpire_impact
            existing.home_bullpen_strength = home_bullpen
            existing.away_bullpen_strength = away_bullpen
            existing.bullpen_convergence = bullpen_convergence
            existing.full_game_projected_total = v4_total
            existing.v3_projected_total = v3_total
            existing.v3_home_win_pct = v3_home_win_pct
            existing.v4_home_win_pct = v4_home_win_pct
            existing.v4_confidence = v4_confidence
            existing.v3_v4_agreement = v3_v4_agreement
            existing.travel_stress_home = home_travel_stress
            existing.travel_stress_away = away_travel_stress
            existing.wind_factor = wind_factor
            existing.temp_f = temp_f
            existing.humidity_pct = humidity_pct
            existing.is_dome = is_dome
            existing.series_game_number = series_game_number
            existing.is_series_opener = is_series_opener
            existing.is_series_finale = is_series_finale
        else:
            db.add(SandboxPredictionV4(
                game_id=game_id,
                game_date=game_date,
                season=season,
                away_team=game.away_team,
                home_team=game.home_team,
                f5_projected_total=f5_projection,
                f5_line=f5_line,
                f5_pick=f5_pick,
                f5_edge_pct=f5_edge_pct,
                umpire_name=umpire_name,
                umpire_run_impact=umpire_impact,
                home_bullpen_strength=home_bullpen,
                away_bullpen_strength=away_bullpen,
                bullpen_convergence=bullpen_convergence,
                full_game_projected_total=v4_total,
                v3_projected_total=v3_total,
                v3_home_win_pct=v3_home_win_pct,
                v4_home_win_pct=v4_home_win_pct,
                v4_confidence=v4_confidence,
                v3_v4_agreement=v3_v4_agreement,
                travel_stress_home=home_travel_stress,
                travel_stress_away=away_travel_stress,
                wind_factor=wind_factor,
                temp_f=temp_f,
                humidity_pct=humidity_pct,
                is_dome=is_dome,
                series_game_number=series_game_number,
                is_series_opener=is_series_opener,
                is_series_finale=is_series_finale,
            ))
        db.commit()
        print(
            f"[v4 sandbox] game_id={game_id} "
            f"v3_total={v3_total:.1f} "
            f"v4_total={v4_total:.1f} "
            f"agreement={v3_v4_agreement} "
            f"travel_stress=home:{home_travel_stress:.2f}/away:{away_travel_stress:.2f}",
            flush=True,
        )

        return {
            "game_id": game_id,
            "v3_total": v3_total,
            "v4_total": v4_total,
            "f5_projection": f5_projection,
            "late_inning_projection": late_inning_projection,
            "f5_line": f5_line,
            "f5_pick": f5_pick,
            "f5_edge_pct": f5_edge_pct,
            "umpire_name": umpire_name,
            "umpire_run_impact": umpire_impact,
            "home_bullpen_strength": home_bullpen,
            "away_bullpen_strength": away_bullpen,
            "bullpen_convergence": bullpen_convergence,
            "v3_home_win_pct": v3_home_win_pct,
            "v4_home_win_pct": v4_home_win_pct,
            "v4_confidence": v4_confidence,
            "v3_v4_agreement": v3_v4_agreement,
            "travel_stress_home": home_travel_stress,
            "travel_stress_away": away_travel_stress,
            "wind_factor": wind_factor,
            "wind_mph": wind_mph,
            "temp_f": temp_f,
            "humidity_pct": humidity_pct,
            "is_dome": is_dome,
            "series_game_number": series_game_number,
            "is_series_opener": is_series_opener,
            "is_series_finale": is_series_finale,
            "away_team": game.away_team,
            "home_team": game.home_team,
        }

    except Exception as e:
        print(f"[v4 sandbox] run_v4_sandbox({game_id}) non-fatal error: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None
