# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the dev server
uvicorn app.main:app --reload

# Install dependencies
pip install -r requirements.txt

# Database migrations
alembic upgrade head           # apply all migrations
alembic revision --autogenerate -m "description"  # generate new migration
alembic downgrade -1           # roll back one step
```

## Environment

Create a `.env` file with:
```
DATABASE_URL=postgresql://...   # Neon Postgres connection string (required)
THE_ODDS_API_KEY=...            # The Odds API key (optional, needed for odds fetching)
```

## Architecture

This is a FastAPI backend that runs MLB game predictions, fetches live odds, computes betting edges, and runs historical backtests. All data is stored in a Neon (serverless Postgres) database via SQLAlchemy.

### Request flow for a typical daily run

`POST /api/daily-run` orchestrates everything in sequence:
1. Sync today's games from the MLB Stats API (`mlb_api.fetch_schedule_for_date`)
2. Fetch team/pitcher stats and run Monte Carlo simulations for each game (`simulator.run_monte_carlo`)
3. Fetch opening odds from The Odds API and store as `snapshot_type=open` (`odds_service.fetch_and_store_odds`)
4. Calculate edges/EV for each game (`edge_service.calculate_edge_for_game`)

`POST /api/pregame-run` runs ~45 min before first pitch:
1. Fetch `snapshot_type=pregame` odds
2. Compute line movement (open vs pregame) — flags "sharp" moves > 4 implied probability points
3. Recalculate edges with movement signal baked in (±1.5–2.0% EV boost/penalty via `ev_math.movement_ev_boost`)

### Scheduler (APScheduler)

`app/scheduler.py` runs two automated jobs (America/New_York timezone):
- **10:00 AM**: Fetch opening odds snapshot
- **10:15 AM**: Schedule per-game pregame jobs (fired 45 min before each first pitch)

The scheduler starts/stops in the FastAPI lifespan context (`app/main.py`).

### Prediction model

`services/simulator.py` — Monte Carlo simulation (1000 iterations default). Each sim draws runs scored using `offense_rpg × era_factor + noise`, where home team gets a +0.25 run boost. Returns win percentages, projected scores, and confidence score.

`services/feature_builder.py` — Builds the feature dict fed into the simulator: ERA/WHIP prefer starter stats over team totals when starter is known.

### EV and edge math (`services/ev_math.py`)

Pure math module with no DB dependencies. Key functions:
- `remove_vig` — normalizes implied probabilities to sum to 1.0
- `calc_ev` — EV% = (p × (decimal_odds - 1)) − (1 − p)
- `calc_edge` — model_prob − vig-removed implied prob
- `kelly_fraction` — quarter-Kelly stake sizing
- `confidence_tier` — classifies plays as "strong" / "medium" / "weak" / None

### Database schema (`app/models/schema.py`)

| Table | Purpose |
|---|---|
| `games` | Today's MLB schedule, synced from MLB Stats API |
| `predictions` | Monte Carlo output per game |
| `game_odds` | Odds snapshots keyed by `(game_id, sportsbook, snapshot_type)` — unique constraint prevents duplicate snapshots |
| `line_movement` | Open→pregame deltas, sharp flags; one row per game (upserted) |
| `edge_results` | Final EV/edge/play recommendation per game, links prediction + odds + movement |
| `backtest_games` | Historical game results with team/starter stats (2022–2024+) |
| `backtest_results` | Logistic regression output — accuracy, CV accuracy, feature coefficients |

### Backtest pipeline

`POST /api/backtest/collect?seasons=2022,2023,2024` — fetches historical data into `backtest_games` (runs as background task).

`POST /api/backtest/run` — trains a logistic regression (`scikit-learn`) on `backtest_games`, stores feature importance and coefficients in `backtest_results`.

### Key conventions

- `game_id` comes directly from the MLB Stats API and is used as the primary key for `games` — no auto-increment.
- `start_time` is stored as an ISO string (not a typed timestamp) in the `games` table; parse with `datetime.fromisoformat()`.
- Odds team name matching uses the last word of the team name (e.g., "New York Yankees" → "Yankees") via `ilike` — see `odds_service._match_game`.
- All scheduler jobs open their own `SessionLocal()` directly; routes use the `get_db` dependency injector.
