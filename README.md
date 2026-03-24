# MLB AI Betting Tool

Sprint 1 Neon/Postgres backend for an MLB betting model platform.

## Features
- FastAPI backend
- Neon Postgres database
- Sync today's MLB games
- Store games in Neon
- Run Monte Carlo prediction for one game
- Store predictions in Neon

## Setup

1. Create a virtual environment
2. Install requirements
3. Add `.env` with your `DATABASE_URL`
4. Run:

```bash
uvicorn app.main:app --reload
