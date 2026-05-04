import enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Date,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.db import Base


class Game(Base):
    __tablename__ = "games"

    game_id = Column(Integer, primary_key=True, index=True)
    game_date = Column(Date, nullable=False, index=True)
    season = Column(Integer, nullable=False, index=True)

    away_team = Column(String, nullable=False)
    home_team = Column(String, nullable=False)

    away_team_id = Column(Integer, nullable=True)
    home_team_id = Column(Integer, nullable=True)

    venue = Column(String, nullable=True)
    status = Column(String, nullable=True)
    start_time = Column(String, nullable=True)

    away_probable_pitcher = Column(String, nullable=True)
    away_pitcher_id = Column(Integer, nullable=True)
    home_probable_pitcher = Column(String, nullable=True)
    home_pitcher_id = Column(Integer, nullable=True)

    final_away_score = Column(Integer, nullable=True)
    final_home_score = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Prediction(Base):
    __tablename__ = "predictions"

    prediction_id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, nullable=False, index=True)

    model_version = Column(String, nullable=False, default="v0.1")
    run_stage = Column(String(32), nullable=False, default="legacy")
    is_active = Column(Boolean, nullable=False, default=True)
    sim_count = Column(Integer, nullable=False, default=1000)
    calibration_result_id = Column(Integer, ForeignKey("backtest_results.id"), nullable=True)

    away_win_pct = Column(Float, nullable=False)
    home_win_pct = Column(Float, nullable=False)

    projected_away_score = Column(Float, nullable=False)
    projected_home_score = Column(Float, nullable=False)
    projected_total = Column(Float, nullable=False)

    confidence_score = Column(Float, nullable=False)
    recommended_side = Column(String, nullable=True)

    home_starter_xera = Column(Float, nullable=True)
    away_starter_xera = Column(Float, nullable=True)
    using_xera = Column(Boolean, nullable=False, default=False)
    kbb_adv = Column(Float, nullable=True)
    park_factor_adv = Column(Float, nullable=True)
    pythagorean_win_pct_adv = Column(Float, nullable=True)

    # Platt-calibrated win probabilities (null until calibration model exists)
    calibrated_home_win_pct = Column(Float, nullable=True)
    calibrated_away_win_pct = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SnapshotType(str, enum.Enum):
    open = "open"
    pregame = "pregame"
    live = "live"


class GameOdds(Base):
    __tablename__ = "game_odds"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False, index=True)
    sportsbook = Column(String(50), nullable=False)
    snapshot_type = Column(Enum(SnapshotType), nullable=False, default=SnapshotType.open)
    fetched_at = Column(DateTime(timezone=True), default=func.now())

    away_ml = Column(Integer)
    home_ml = Column(Integer)

    total_line = Column(Numeric(4, 1))
    over_odds = Column(Integer)
    under_odds = Column(Integer)

    runline_away = Column(Numeric(3, 1))
    runline_odds = Column(Integer)

    __table_args__ = (
        UniqueConstraint("game_id", "sportsbook", "snapshot_type", name="uq_odds_per_snapshot_type"),
    )


class LineMovement(Base):
    __tablename__ = "line_movement"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False, unique=True)
    sportsbook = Column(String(50), nullable=False)
    calculated_at = Column(DateTime(timezone=True), default=func.now())

    open_away_ml = Column(Integer)
    open_home_ml = Column(Integer)
    open_total = Column(Numeric(4, 1))

    pregame_away_ml = Column(Integer)
    pregame_home_ml = Column(Integer)
    pregame_total = Column(Numeric(4, 1))

    away_prob_move = Column(Numeric(5, 4))
    home_prob_move = Column(Numeric(5, 4))
    total_move = Column(Numeric(4, 1))

    sharp_away = Column(Boolean, default=False)
    sharp_home = Column(Boolean, default=False)
    total_steam_over = Column(Boolean, default=False)
    total_steam_under = Column(Boolean, default=False)


class BacktestGame(Base):
    __tablename__ = "backtest_games"

    game_id = Column(Integer, primary_key=True)
    game_date = Column(Date, nullable=False)
    season = Column(Integer, nullable=False, index=True)
    home_team_id = Column(Integer, nullable=False)
    away_team_id = Column(Integer, nullable=False)
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    venue = Column(String, nullable=True)
    game_start_time = Column(DateTime(timezone=True), nullable=True)
    feature_cutoff_time = Column(DateTime(timezone=True), nullable=True)
    feature_cutoff_policy = Column(String, nullable=True)
    home_score = Column(Integer, nullable=True)
    away_score = Column(Integer, nullable=True)
    home_win = Column(Boolean, nullable=True)
    home_starter_id = Column(Integer, nullable=True)
    away_starter_id = Column(Integer, nullable=True)
    home_starter_name = Column(String, nullable=True)
    away_starter_name = Column(String, nullable=True)
    home_starter_era = Column(Float, nullable=True)
    away_starter_era = Column(Float, nullable=True)
    home_team_era = Column(Float, nullable=True)
    away_team_era = Column(Float, nullable=True)
    home_team_ops = Column(Float, nullable=True)
    away_team_ops = Column(Float, nullable=True)
    home_team_whip = Column(Float, nullable=True)
    away_team_whip = Column(Float, nullable=True)
    home_win_pct = Column(Float, nullable=True)
    away_win_pct = Column(Float, nullable=True)
    home_games_played = Column(Integer, nullable=True)
    away_games_played = Column(Integer, nullable=True)
    home_run_diff = Column(Integer, nullable=True)
    away_run_diff = Column(Integer, nullable=True)
    home_pythagorean_win_pct = Column(Float, nullable=True)
    away_pythagorean_win_pct = Column(Float, nullable=True)
    home_bullpen_era = Column(Float, nullable=True)
    away_bullpen_era = Column(Float, nullable=True)
    # Statcast team batting / speed metrics (Phase 3 — collected separately)
    home_exit_velo    = Column(Float, nullable=True)
    away_exit_velo    = Column(Float, nullable=True)
    home_barrel_rate  = Column(Float, nullable=True)
    away_barrel_rate  = Column(Float, nullable=True)
    home_hard_hit_rate = Column(Float, nullable=True)
    away_hard_hit_rate = Column(Float, nullable=True)
    home_sprint_speed  = Column(Float, nullable=True)
    away_sprint_speed  = Column(Float, nullable=True)
    # K-BB differential per 9 innings for each starter (k9 − bb9)
    home_starter_kbb   = Column(Float, nullable=True)
    away_starter_kbb   = Column(Float, nullable=True)
    home_starter_whip = Column(Float, nullable=True)
    away_starter_whip = Column(Float, nullable=True)
    home_starter_starts = Column(Integer, nullable=True)
    away_starter_starts = Column(Integer, nullable=True)
    odds_snapshot_type = Column(String, nullable=True)
    odds_snapshot_policy = Column(String, nullable=True)
    odds_row_id = Column(Integer, nullable=True)
    odds_fetched_at = Column(DateTime(timezone=True), nullable=True)
    odds_away_ml = Column(Integer, nullable=True)
    odds_home_ml = Column(Integer, nullable=True)
    odds_total = Column(Numeric(4, 1), nullable=True)
    features_complete = Column(Boolean, nullable=False, default=False)
    odds_complete = Column(Boolean, nullable=False, default=False)
    incomplete_reasons_json = Column(Text, nullable=True)
    collected_at = Column(DateTime(timezone=True), server_default=func.now())


class BacktestResult(Base):
    __tablename__ = "backtest_results"

    id = Column(Integer, primary_key=True)
    run_at = Column(DateTime(timezone=True), server_default=func.now())
    seasons = Column(String, nullable=False)
    n_games = Column(Integer, nullable=False)
    accuracy = Column(Float, nullable=False)
    cv_accuracy = Column(Float, nullable=False)
    log_loss = Column(Float, nullable=True)
    brier_score = Column(Float, nullable=True)
    calibration_params_json = Column(String, nullable=True)  # JSON {"a": float, "b": float}
    coefficients_json = Column(String, nullable=False)
    feature_ranks_json = Column(String, nullable=False)
    dataset_summary_json = Column(Text, nullable=True)
    validation_summary_json = Column(Text, nullable=True)
    limitations_json = Column(Text, nullable=True)


class EdgeResult(Base):
    __tablename__ = "edge_results"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False, index=True)
    prediction_id = Column(Integer, ForeignKey("predictions.prediction_id"), nullable=False)
    odds_id = Column(Integer, ForeignKey("game_odds.id"), nullable=False)
    run_stage = Column(String(32), nullable=False, default="legacy")
    is_active = Column(Boolean, nullable=False, default=True)
    movement_id = Column(Integer, ForeignKey("line_movement.id"), nullable=True)
    calculated_at = Column(DateTime(timezone=True), default=func.now())

    model_away_win_pct = Column(Numeric(5, 4))
    model_home_win_pct = Column(Numeric(5, 4))
    implied_away_pct = Column(Numeric(5, 4))
    implied_home_pct = Column(Numeric(5, 4))

    edge_away = Column(Numeric(5, 4))
    edge_home = Column(Numeric(5, 4))

    ev_away = Column(Numeric(6, 4))
    ev_home = Column(Numeric(6, 4))

    movement_boost = Column(Numeric(5, 4), default=0)

    model_total = Column(Numeric(4, 1))
    book_total = Column(Numeric(4, 1))
    total_edge = Column(Numeric(6, 2))
    ev_over = Column(Numeric(6, 4))
    ev_under = Column(Numeric(6, 4))

    recommended_play = Column(String(20))
    confidence_tier = Column(String(10))
    edge_pct = Column(Numeric(5, 4))
    movement_direction = Column(String(20), nullable=True)
    pitching_edge_score = Column(Float, nullable=True)
    sportsbook = Column(String(50), nullable=True)
    odds_snapshot_type = Column(String(20), nullable=True)
    away_ml = Column(Integer, nullable=True)
    home_ml = Column(Integer, nullable=True)
    over_odds = Column(Integer, nullable=True)
    under_odds = Column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("game_id", "prediction_id", name="uq_edge_game_prediction"),
    )


class BetAlert(Base):
    __tablename__ = "bet_alerts"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False, index=True)
    prediction_id = Column(Integer, ForeignKey("predictions.prediction_id"), nullable=False, index=True)
    edge_result_id = Column(Integer, ForeignKey("edge_results.id"), nullable=False, index=True)

    alert_time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    game_date = Column(Date, nullable=False, index=True)

    play = Column(String(20), nullable=False)
    edge_pct = Column(Numeric(8, 4), nullable=False)
    ev = Column(Numeric(8, 4), nullable=False)
    confidence = Column(String(10), nullable=False)

    synopsis = Column(Text, nullable=False)
    rationale_json = Column(Text, nullable=True)

    sent_to = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)

    final_away_score = Column(Integer, nullable=True)
    final_home_score = Column(Integer, nullable=True)
    bet_result = Column(String(10), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("game_id", "edge_result_id", name="uq_bet_alert_game_edge"),
    )


class GameOutcomeReview(Base):
    __tablename__ = "game_outcomes_review"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False, index=True)
    prediction_id = Column(Integer, ForeignKey("predictions.prediction_id"), nullable=False, index=True)
    edge_result_id = Column(Integer, ForeignKey("edge_results.id"), nullable=True, index=True)
    bet_alert_id = Column(Integer, ForeignKey("bet_alerts.id"), nullable=True, index=True)

    game_date = Column(Date, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    pre_game_synopsis = Column(Text, nullable=True)
    actual_outcome_summary = Column(Text, nullable=False)

    recommended_play = Column(String(20), nullable=True)
    confidence_tier = Column(String(10), nullable=True)

    model_away_win_pct = Column(Numeric(8, 4), nullable=True)
    model_home_win_pct = Column(Numeric(8, 4), nullable=True)
    projected_away_score = Column(Numeric(6, 2), nullable=True)
    projected_home_score = Column(Numeric(6, 2), nullable=True)
    model_total = Column(Numeric(8, 4), nullable=True)
    book_total = Column(Numeric(8, 4), nullable=True)
    edge_pct = Column(Numeric(8, 4), nullable=True)
    ev = Column(Numeric(8, 4), nullable=True)
    movement_direction = Column(String(20), nullable=True)

    final_away_score = Column(Integer, nullable=False)
    final_home_score = Column(Integer, nullable=False)
    winning_side = Column(String(10), nullable=False)
    bet_result = Column(String(10), nullable=False)
    was_model_correct = Column(Boolean, nullable=False, default=False)
    total_correct = Column(Boolean, nullable=True)

    top_factors_predicted = Column(Text, nullable=True)
    top_factors_actual = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("game_id", "prediction_id", name="uq_outcome_review_prediction"),
    )


# ── v0.4 Sandbox Tables ──────────────────────────────────────────────────────

class UmpireAssignmentV4(Base):
    __tablename__ = "umpire_assignments_v4"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=True)
    umpire_name = Column(String(100), nullable=False)
    historical_k_rate_delta = Column(Float, default=0.0)
    run_expectancy_impact = Column(Float, default=0.0)
    season = Column(Integer, nullable=True)
    collected_at = Column(DateTime(timezone=True), server_default=func.now())


class F5LineV4(Base):
    __tablename__ = "f5_lines_v4"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    f5_over_under_line = Column(Float, nullable=True)
    f5_over_odds = Column(Integer, default=-110)
    f5_under_odds = Column(Integer, default=-110)


class RelieverWorkload(Base):
    __tablename__ = "reliever_workload"

    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, nullable=True)
    team_id = Column(Integer, nullable=True)
    date = Column(Date, nullable=False)
    pitches_thrown = Column(Integer, default=0)
    innings_pitched = Column(Float, nullable=True)
    days_rest = Column(Integer, default=99)
    appearances_last_3_days = Column(Integer, default=0)
    note = Column(String(10), nullable=True)
    player_name = Column(String(100), nullable=True)
    collected_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("player_id", "date", name="uq_reliever_workload_player_date"),
    )


class ManagerTendency(Base):
    __tablename__ = "manager_tendencies"

    team_id = Column(Integer, primary_key=True)
    manager_name = Column(String(100), nullable=True)
    b2b_usage_rate = Column(Float, default=0.3)
    strict_pitch_cap = Column(Integer, default=30)
    avg_relievers_per_game = Column(Float, nullable=True)
    avg_bullpen_pitches_per_game = Column(Float, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SandboxPredictionV4(Base):
    __tablename__ = "sandbox_predictions_v4"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=True)
    game_date = Column(Date, nullable=True)
    season = Column(Integer, default=2026)
    away_team = Column(String(100), nullable=True)
    home_team = Column(String(100), nullable=True)

    f5_projected_total = Column(Float, nullable=True)
    f5_line = Column(Float, nullable=True)
    f5_pick = Column(String(10), nullable=True)
    f5_edge_pct = Column(Float, nullable=True)

    umpire_name = Column(String(100), nullable=True)
    umpire_run_impact = Column(Float, default=0.0)

    home_bullpen_strength = Column(Float, default=1.0)
    away_bullpen_strength = Column(Float, default=1.0)
    bullpen_convergence = Column(Boolean, default=False)

    full_game_projected_total = Column(Float, nullable=True)

    v3_projected_total = Column(Float, nullable=True)
    v3_home_win_pct = Column(Float, nullable=True)
    v4_home_win_pct = Column(Float, nullable=True)
    v4_confidence = Column(Float, nullable=True)
    v3_v4_agreement = Column(Boolean, default=False)

    # v0.5 travel stress (0.0 = no stress, 1.0 = maximum)
    travel_stress_home = Column(Float, nullable=True)
    travel_stress_away = Column(Float, nullable=True)

    # v0.5 weather/wind (-1.0 = strong in from CF, +1.0 = strong out to CF)
    wind_factor = Column(Float, nullable=True)
    temp_f = Column(Float, nullable=True)
    humidity_pct = Column(Float, nullable=True)
    is_dome = Column(Boolean, default=False)

    # v0.5 series position
    series_game_number = Column(Integer, nullable=True)
    is_series_opener = Column(Boolean, default=False)
    is_series_finale = Column(Boolean, default=False)

    # v0.5 home-dog / public-fade factor
    public_bias_edge = Column(Float, nullable=True)

    f5_result = Column(String(10), nullable=True)
    full_game_result = Column(String(10), nullable=True)
    f5_graded_at = Column(DateTime(timezone=True), nullable=True)
    full_game_graded_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
