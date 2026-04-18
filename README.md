# MLB AI Betting Tool

Neon/Postgres-backed FastAPI service for syncing MLB games, generating model predictions, computing edges, and emitting alerts.

## Features

- FastAPI backend
- Neon Postgres database
- Daily and pregame pipeline routes
- Odds sync and edge calculation
- Backtesting, reviews, and dashboard/status endpoints
- Admin freshness and dashboard-metric backfill endpoints
- Dashboard pages for war room, bets, system, simulator, and admin

## Setup

1. Create a virtual environment:

```bash
python -m venv .venv
```

2. Activate it:

```bash
.venv\Scripts\activate
```

3. Install requirements:

```bash
pip install -r requirements.txt
```

4. Copy `.env.example` to `.env` and fill in at least `DATABASE_URL`.

5. Run migrations:

```bash
alembic upgrade head
```

6. Run the API:

```bash
uvicorn app.main:app --reload
```

7. Optional: repopulate today’s persisted dashboard metrics:

```bash
python scripts/backfill_prediction_dashboard_metrics.py
```

## Environment

Required:

- `DATABASE_URL`

Optional:

- `THE_ODDS_API_KEY`
- `DISCORD_WEBHOOK_URL`
- `DISCORD_SANDBOX_WEBHOOK_URL`
- `ALERT_DESTINATION`
- `ALERT_MIN_EV`
- `ALERT_MIN_EDGE`
- `ALERT_CONFIDENCE_LEVELS`
- `POSTGAME_LOOKBACK_HOURS`
- `CLOUDBET_API_KEY`
- `CLOUDBET_API_URL`
- `BETTING_ENABLED`
- `BETTING_MODE`
- `BOOK_PROVIDER`
- `DEFAULT_BANKROLL`
- `KILL_SWITCH`

## Local Run Checklist

1. Copy `.env.example` to `.env`
2. Run `alembic upgrade head`
3. Start the API with `uvicorn app.main:app --reload`
4. Verify `/dashboard`, `/bets`, `/system`, and `/admin`
5. Check `/api/admin/freshness` for the latest pipeline timestamps
6. Use `POST /api/admin/backfill/prediction-dashboard-metrics` if dashboard-derived fields need a backfill

## Tests

The app loads configuration on import, so set `DATABASE_URL` before running tests.

Example:

```bash
set DATABASE_URL=sqlite:///./test.db
python -m unittest tests.test_odds_service tests.test_edge_hardening tests.test_dashboard_pipeline tests.test_routes_and_admin
```
