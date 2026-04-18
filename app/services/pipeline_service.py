import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.schema import Game
from app.services.alert_service import create_and_send_alerts_for_today
from app.services.backtest_service import (
    apply_calibration,
    build_live_feature_vector,
    get_latest_calibration_result,
    score_logistic_home_probability,
)
from app.services.edge_service import calculate_all_edges_today
from app.services.feature_builder import build_team_features
from app.services.mlb_api import fetch_bullpen_stats, fetch_pitcher_stats, fetch_schedule_for_date, fetch_team_stats
from app.services.model_diagnostics import summarize_edge_diagnostics, summarize_probability_diagnostics
from app.services.odds_service import (
    SnapshotType,
    compute_line_movement,
    fetch_and_store_odds,
    get_latest_odds_snapshot,
    get_market_home_probability,
    is_odds_snapshot_fresh,
)
from app.services.prediction_service import deactivate_stale_active_predictions, store_prediction
from app.services.review_service import resolve_completed_games
from app.services.simulator import MODEL_VERSION, run_monte_carlo
from app.services.statcast_service import fetch_team_statcast

ET = ZoneInfo("America/New_York")


def sync_games_for_date(db: Session, target_date: date) -> dict:
    games = fetch_schedule_for_date(str(target_date))
    synced = 0
    for g in games:
        existing = db.query(Game).filter(Game.game_id == g["game_id"]).first()
        if existing:
            existing.status = g["status"]
            existing.start_time = g["start_time"]
            existing.away_probable_pitcher = g["away_probable_pitcher"]
            existing.away_pitcher_id = g["away_pitcher_id"]
            existing.home_probable_pitcher = g["home_probable_pitcher"]
            existing.home_pitcher_id = g["home_pitcher_id"]
            existing.final_away_score = g["final_away_score"]
            existing.final_home_score = g["final_home_score"]
        else:
            db.add(
                Game(
                    game_id=g["game_id"],
                    game_date=date.fromisoformat(g["game_date"]),
                    season=g["season"],
                    away_team=g["away_team"],
                    home_team=g["home_team"],
                    away_team_id=g["away_team_id"],
                    home_team_id=g["home_team_id"],
                    venue=g["venue"],
                    status=g["status"],
                    start_time=g["start_time"],
                    away_probable_pitcher=g["away_probable_pitcher"],
                    away_pitcher_id=g["away_pitcher_id"],
                    home_probable_pitcher=g["home_probable_pitcher"],
                    home_pitcher_id=g["home_pitcher_id"],
                    final_away_score=g["final_away_score"],
                    final_home_score=g["final_home_score"],
                )
            )
            synced += 1
    db.commit()
    return {"status": "ok", "total": len(games), "new": synced}


def run_predictions_for_date(
    db: Session,
    target_date: date,
    *,
    run_stage: str,
    diagnostic_label: str,
    include_sandbox: bool = False,
) -> dict:
    games = db.query(Game).filter(Game.game_date == target_date).all()
    cal_result = get_latest_calibration_result(db)
    cal_params = None
    cal_result_id = None
    if cal_result and cal_result.calibration_params_json:
        cal_params = json.loads(cal_result.calibration_params_json)
        cal_result_id = cal_result.id

    ok: list[int] = []
    errors: list[dict] = []
    probability_results: list[dict] = []

    for game in games:
        try:
            away_raw = fetch_team_stats(team_id=game.away_team_id, season=game.season)
            home_raw = fetch_team_stats(team_id=game.home_team_id, season=game.season)
            away_starter = fetch_pitcher_stats(game.away_pitcher_id, game.season, include_xera=True) if game.away_pitcher_id else None
            home_starter = fetch_pitcher_stats(game.home_pitcher_id, game.season, include_xera=True) if game.home_pitcher_id else None
            away_bullpen = fetch_bullpen_stats(game.away_team_id, game.season)
            home_bullpen = fetch_bullpen_stats(game.home_team_id, game.season)
            away_statcast = fetch_team_statcast(game.away_team_id, game.season)
            home_statcast = fetch_team_statcast(game.home_team_id, game.season)

            away_features = build_team_features(
                away_raw,
                starter_stats=away_starter,
                bullpen_stats=away_bullpen,
                statcast_team=away_statcast,
            )
            home_features = build_team_features(
                home_raw,
                starter_stats=home_starter,
                venue=game.venue,
                bullpen_stats=home_bullpen,
                statcast_team=home_statcast,
            )

            snapshot_type = SnapshotType.open if run_stage == "daily_open" else SnapshotType.pregame
            latest_odds = get_latest_odds_snapshot(db, game_id=game.game_id, snapshot_type=snapshot_type)
            market_home_prob = get_market_home_probability(latest_odds) if latest_odds and is_odds_snapshot_fresh(latest_odds) else None
            live_features = build_live_feature_vector(home_features, away_features)
            logistic_home_prob = score_logistic_home_probability(
                live_features,
                cal_result,
            )
            result = run_monte_carlo(
                away_team=away_features,
                home_team=home_features,
                sim_count=1000,
                market_home_prob=market_home_prob,
                logistic_home_prob=logistic_home_prob,
            )

            cal_home = cal_away = None
            if cal_params:
                cal_home, cal_away = apply_calibration(
                    result["home_win_pct"],
                    result["away_win_pct"],
                    cal_params,
                )

            store_prediction(
                db,
                game_id=game.game_id,
                model_version=MODEL_VERSION,
                run_stage=run_stage,
                sim_count=result["sim_count"],
                away_win_pct=result["away_win_pct"],
                home_win_pct=result["home_win_pct"],
                calibrated_home_win_pct=cal_home,
                calibrated_away_win_pct=cal_away,
                projected_away_score=result["projected_away_score"],
                projected_home_score=result["projected_home_score"],
                projected_total=result["projected_total"],
                confidence_score=result["confidence_score"],
                recommended_side=result["recommended_side"],
                home_starter_xera=home_features.get("starter_xera"),
                away_starter_xera=away_features.get("starter_xera"),
                using_xera=bool(home_features.get("using_xera") or away_features.get("using_xera")),
                kbb_adv=live_features.get("kbb_adv"),
                park_factor_adv=live_features.get("park_factor_adv"),
                pythagorean_win_pct_adv=live_features.get("pythagorean_win_pct_adv"),
                calibration_result_id=cal_result_id,
            )
            ok.append(game.game_id)
            probability_results.append(result)

            if include_sandbox:
                try:
                    from app.services.sandbox_simulator import run_v4_sandbox

                    v4 = run_v4_sandbox(game.game_id, db)
                    if v4:
                        print(
                            f"[v4 sandbox] game {game.game_id}: "
                            f"v3={v4['v3_total']:.1f} v4={v4['v4_total']:.1f} "
                            f"agreement={v4['v3_v4_agreement']}",
                            flush=True,
                        )
                    try:
                        from app.services.sandbox_alerts import send_sandbox_alert

                        if v4 and v4.get("v4_confidence", 0) > 0.05:
                            send_sandbox_alert(v4, game, db)
                    except Exception as alert_error:
                        print(f"[v4 alert] non-fatal: {alert_error}", flush=True)
                except Exception as sandbox_error:
                    print(f"[v4 sandbox] non-fatal error game {game.game_id}: {sandbox_error}", flush=True)
        except Exception as exc:
            db.rollback()
            errors.append({"game_id": game.game_id, "error": str(exc)})

    summarize_probability_diagnostics(probability_results, label=diagnostic_label)
    return {
        "status": "ok" if not errors else "partial",
        "ran": len(ok),
        "errors": errors,
    }


async def sync_odds_for_snapshot(
    db: Session,
    *,
    snapshot_type: SnapshotType,
    label: str,
) -> tuple[list, dict]:
    try:
        stored = await fetch_and_store_odds(db, snapshot_type=snapshot_type)
        return stored, {"status": "ok", label: len(stored)}
    except Exception as exc:
        return [], {"status": "error", "detail": str(exc)}


def calculate_edges_for_today(
    db: Session,
    *,
    run_stage: str,
    snapshot_type: SnapshotType,
    odds_rows: list | None,
    diagnostic_label: str,
) -> dict:
    try:
        edge_results = calculate_all_edges_today(
            db,
            run_stage=run_stage,
            snapshot_type=snapshot_type,
            odds_rows=odds_rows,
            fallback_policy="reuse_fresh_same_stage",
        )
        created = [r for r in edge_results if r["status"] == "created"]
        skipped = [r for r in edge_results if r["status"] != "created"]
        skip_reasons: dict[str, int] = {}
        for row in skipped:
            skip_reasons[row["reason"]] = skip_reasons.get(row["reason"], 0) + 1
        summarize_edge_diagnostics(edge_results, label=diagnostic_label)
        return {
            "status": "ok",
            "calculated": len(created),
            "skipped": len(skipped),
            "skip_reasons": skip_reasons,
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


def compute_line_movements_for_date(db: Session, target_date: date) -> dict:
    game_records = db.query(Game).filter(Game.game_date == target_date).all()
    ok, errors = [], []
    for game in game_records:
        try:
            movement = compute_line_movement(db, game.game_id)
            if movement:
                ok.append(game.game_id)
        except Exception as exc:
            errors.append({"game_id": game.game_id, "error": str(exc)})
    return {
        "status": "ok" if not errors else "partial",
        "computed": len(ok),
        "errors": errors,
    }


def send_daily_alerts(db: Session) -> dict:
    try:
        result = create_and_send_alerts_for_today(db)
        return {"status": "ok", **result}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


async def run_daily_pipeline(db: Session, target_date: date | None = None) -> dict:
    today = target_date or datetime.now(ET).date()
    results = {"date": str(today), "steps": {}}

    try:
        resolve_result = resolve_completed_games(db)
        results["steps"]["resolve_yesterday"] = {"status": "ok", **resolve_result}
    except Exception as exc:
        results["steps"]["resolve_yesterday"] = {"status": "error", "detail": str(exc)}

    try:
        results["steps"]["sync_games"] = sync_games_for_date(db, today)
    except Exception as exc:
        results["steps"]["sync_games"] = {"status": "error", "detail": str(exc)}

    results["steps"]["prediction_cleanup"] = {
        "status": "ok",
        "deactivated": deactivate_stale_active_predictions(db, keep_on_or_after=today),
    }
    results["steps"]["monte_carlo"] = run_predictions_for_date(
        db,
        today,
        run_stage="daily_open",
        diagnostic_label="daily-run",
    )

    stored, odds_result = await sync_odds_for_snapshot(
        db,
        snapshot_type=SnapshotType.open,
        label="stored",
    )
    results["steps"]["sync_odds"] = odds_result
    results["steps"]["edges"] = calculate_edges_for_today(
        db,
        run_stage="daily_open",
        snapshot_type=SnapshotType.open,
        odds_rows=stored,
        diagnostic_label="daily-run",
    )
    results["steps"]["alerts"] = send_daily_alerts(db)
    return results


async def run_pregame_pipeline(db: Session, target_date: date | None = None) -> dict:
    today = target_date or datetime.now(ET).date()
    results = {"date": str(today), "steps": {}}

    stored, odds_result = await sync_odds_for_snapshot(
        db,
        snapshot_type=SnapshotType.pregame,
        label="stored",
    )
    results["steps"]["sync_pregame_odds"] = odds_result
    results["steps"]["line_movement"] = compute_line_movements_for_date(db, today)
    results["steps"]["edges"] = calculate_edges_for_today(
        db,
        run_stage="pregame",
        snapshot_type=SnapshotType.pregame,
        odds_rows=stored,
        diagnostic_label="pregame-run",
    )
    results["steps"]["alerts"] = send_daily_alerts(db)
    return results
