# MLB AI Betting Tool

Neon/Postgres-backed FastAPI service for syncing MLB games, generating model predictions, computing edges, and emitting alerts.

## Features

- FastAPI backend
- Neon Postgres database
- Daily and pregame pipeline routes
- Odds sync and edge calculation
- Backtesting, reviews, and dashboard/status endpoints

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

5. Run the API:

```bash
uvicorn app.main:app --reload
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

## Tests

The app loads configuration on import, so set `DATABASE_URL` before running tests.

Example:

```bash
set DATABASE_URL=sqlite:///./test.db
python -m unittest tests.test_odds_service tests.test_edge_hardening
```
