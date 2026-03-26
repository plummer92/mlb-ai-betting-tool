"""
Backtest service — collects historical game data from the MLB Stats API,
runs the Monte Carlo simulator on each game using that season's team stats,
then fits a logistic regression to find which features actually predict outcomes.
"""
import json
import logging
from datetime import date

import requests
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss as sk_log_loss
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import StandardScaler
from sqlalchemy.orm import Session

from app.config import MLB_API_BASE
from app.models.schema import BacktestGame, BacktestResult
from app.services.mlb_api import fetch_pitcher_stats, fetch_team_stats

logger = logging.getLogger(__name__)

# Features used for logistic regression (all expressed as home-minus-away advantages)
FEATURE_NAMES = [
    "home_era_adv",    # away_era  - home_era  (positive = home pitches better)
    "home_whip_adv",   # away_whip - home_whip
    "home_ops_adv",    # home_ops  - away_ops
    "home_win_pct",
    "away_win_pct",
    "home_starter_era_adv",  # away_starter_era - home_starter_era (may be 0 if no data)
]


# ── Season collection ─────────────────────────────────────────────────────────

def _fetch_season_schedule(season: int) -> list[dict]:
    """Return all completed regular-season games for a given season."""
    url = (
        f"{MLB_API_BASE}/schedule"
        f"?sportId=1&season={season}&gameType=R"
        f"&startDate={season}-03-01&endDate={season}-11-30"
        f"&hydrate=team,linescore,probablePitcher"
    )
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    payload = resp.json()

    games = []
    for day in payload.get("dates", []):
        for g in day.get("games", []):
            status = g.get("status", {}).get("abstractGameState", "")
            if status != "Final":
                continue
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            away_pitcher = away.get("probablePitcher") or {}
            home_pitcher = home.get("probablePitcher") or {}
            away_record = away.get("leagueRecord", {})
            home_record = home.get("leagueRecord", {})
            away_w = away_record.get("wins", 0) or 0
            away_l = away_record.get("losses", 0) or 0
            home_w = home_record.get("wins", 0) or 0
            home_l = home_record.get("losses", 0) or 0
            games.append({
                "game_id":         g["gamePk"],
                "game_date":       day["date"],
                "season":          season,
                "away_team":       away["team"]["name"],
                "away_team_id":    away["team"]["id"],
                "home_team":       home["team"]["name"],
                "home_team_id":    home["team"]["id"],
                "venue":           g.get("venue", {}).get("name"),
                "away_score":      away.get("score"),
                "home_score":      home.get("score"),
                "home_win":        (home.get("score", 0) or 0) > (away.get("score", 0) or 0),
                "away_starter_id": away_pitcher.get("id"),
                "home_starter_id": home_pitcher.get("id"),
                "away_starter_name": away_pitcher.get("fullName"),
                "home_starter_name": home_pitcher.get("fullName"),
                "away_win_pct": away_w / (away_w + away_l) if (away_w + away_l) > 0 else 0.5,
                "home_win_pct": home_w / (home_w + home_l) if (home_w + home_l) > 0 else 0.5,
            })
    return games


def collect_season(db: Session, season: int) -> int:
    """
    Fetch all completed games for a season, enrich with team + starter stats,
    and upsert into backtest_games. Returns number of games processed.
    """
    logger.info("Collecting backtest data for season %s", season)
    games = _fetch_season_schedule(season)
    if not games:
        return 0

    # Cache team stats to avoid redundant API calls (30 teams × 2 groups per season)
    team_stats_cache: dict[int, dict] = {}

    def _team_stats(team_id: int) -> dict:
        if team_id not in team_stats_cache:
            team_stats_cache[team_id] = fetch_team_stats(team_id, season)
        return team_stats_cache[team_id]

    processed = 0
    for g in games:
        try:
            away_stats = _team_stats(g["away_team_id"])
            home_stats = _team_stats(g["home_team_id"])

            away_starter = fetch_pitcher_stats(g["away_starter_id"], season) if g["away_starter_id"] else None
            home_starter = fetch_pitcher_stats(g["home_starter_id"], season) if g["home_starter_id"] else None

            away_run_diff = away_stats["runs"] - away_stats["runs_allowed"]
            home_run_diff = home_stats["runs"] - home_stats["runs_allowed"]

            row_data = dict(
                game_id=g["game_id"],
                game_date=date.fromisoformat(g["game_date"]),
                season=season,
                away_team=g["away_team"],
                away_team_id=g["away_team_id"],
                home_team=g["home_team"],
                home_team_id=g["home_team_id"],
                venue=g["venue"],
                away_score=g["away_score"],
                home_score=g["home_score"],
                home_win=g["home_win"],
                away_starter_id=g["away_starter_id"],
                home_starter_id=g["home_starter_id"],
                away_starter_name=g["away_starter_name"],
                home_starter_name=g["home_starter_name"],
                away_starter_era=away_starter["era"] if away_starter else None,
                home_starter_era=home_starter["era"] if home_starter else None,
                away_team_era=away_stats["era"],
                home_team_era=home_stats["era"],
                away_team_ops=away_stats["ops"],
                home_team_ops=home_stats["ops"],
                away_team_whip=away_stats["whip"],
                home_team_whip=home_stats["whip"],
                away_win_pct=g["away_win_pct"],
                home_win_pct=g["home_win_pct"],
                away_run_diff=away_run_diff,
                home_run_diff=home_run_diff,
            )

            stmt = pg_insert(BacktestGame).values(**row_data)
            stmt = stmt.on_conflict_do_update(
                index_elements=["game_id"],
                set_={k: v for k, v in row_data.items() if k != "game_id"},
            )
            db.execute(stmt)

            processed += 1
            if processed % 200 == 0:
                db.commit()
                logger.info("  ...%d games committed", processed)

        except Exception as exc:
            logger.warning("Skipping game %s: %s", g["game_id"], exc)
            db.rollback()

    db.commit()
    logger.info("Season %s: %d games stored", season, processed)
    return processed


# ── Logistic regression ───────────────────────────────────────────────────────

def run_logistic_regression(db: Session, seasons: list[int]) -> BacktestResult:
    """
    Pull backtest_games for the given seasons, build feature matrix,
    fit logistic regression, and store results.
    """
    rows = (
        db.query(BacktestGame)
        .filter(BacktestGame.season.in_(seasons), BacktestGame.home_win.isnot(None))
        .all()
    )
    if len(rows) < 50:
        raise ValueError(f"Too few rows for regression ({len(rows)}). Run collect first.")

    X, y = [], []
    for r in rows:
        home_era_adv  = (r.away_team_era or 4.2)  - (r.home_team_era or 4.2)
        home_whip_adv = (r.away_team_whip or 1.3)  - (r.home_team_whip or 1.3)
        home_ops_adv  = (r.home_team_ops or 0.72) - (r.away_team_ops or 0.72)
        home_win_pct  = r.home_win_pct or 0.5
        away_win_pct  = r.away_win_pct or 0.5
        home_starter_era_adv = (
            (r.away_starter_era or r.away_team_era or 4.2)
            - (r.home_starter_era or r.home_team_era or 4.2)
        )
        X.append([home_era_adv, home_whip_adv, home_ops_adv,
                   home_win_pct, away_win_pct, home_starter_era_adv])
        y.append(int(r.home_win))

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = LogisticRegression(max_iter=1000, random_state=42)
    model.fit(X_scaled, y)

    y_pred_proba = model.predict_proba(X_scaled)[:, 1]
    y_pred = model.predict(X_scaled)

    accuracy    = float(sum(a == b for a, b in zip(y_pred, y)) / len(y))
    loss        = float(sk_log_loss(y, y_pred_proba))
    cv_scores   = cross_val_score(model, X_scaled, y, cv=5, scoring="accuracy")
    cv_accuracy = float(cv_scores.mean())

    coefs = dict(zip(FEATURE_NAMES, [round(float(c), 6) for c in model.coef_[0]]))
    ranked = sorted(coefs.items(), key=lambda x: abs(x[1]), reverse=True)

    result = BacktestResult(
        seasons=",".join(str(s) for s in sorted(seasons)),
        n_games=len(rows),
        accuracy=round(accuracy, 4),
        cv_accuracy=round(cv_accuracy, 4),
        log_loss=round(loss, 4),
        coefficients_json=json.dumps(coefs),
        feature_ranks_json=json.dumps([{"feature": f, "coef": c} for f, c in ranked]),
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    apply_backtest_weights(result)
    return result


def apply_backtest_weights(result: BacktestResult) -> None:
    """
    Extract ERA/WHIP/OPS coefficients from a backtest result and push them
    into the simulator as updated feature weights.
    """
    from app.services.simulator import set_weights

    coefs = json.loads(result.coefficients_json)
    era_abs  = abs(coefs.get("home_era_adv", 0.42))
    whip_abs = abs(coefs.get("home_whip_adv", 0.36))
    ops_abs  = abs(coefs.get("home_ops_adv", 0.15))

    # Guard against all-zero coefficients (degenerate regression)
    if era_abs + whip_abs + ops_abs == 0:
        return

    set_weights(era_abs, whip_abs, ops_abs)
    logger.info(
        "Simulator weights updated from backtest — ERA: %.4f  WHIP: %.4f  OPS: %.4f",
        era_abs, whip_abs, ops_abs,
    )
