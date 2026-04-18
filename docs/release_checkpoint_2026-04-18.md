# Release Checkpoint: Dashboard Pipeline Refactor

## Summary

- Moved daily and pregame orchestration into `app/services/pipeline_service.py`
- Made `GET /api/edges/today` database-first instead of recomputing live feature data
- Persisted dashboard metrics on predictions:
  - `kbb_adv`
  - `park_factor_adv`
  - `pythagorean_win_pct_adv`
- Persisted deep-dive odds context on edge rows:
  - `sportsbook`
  - `odds_snapshot_type`
  - `away_ml`
  - `home_ml`
  - `over_odds`
  - `under_odds`
- Added a top bets page and a dedicated admin page
- Added `GET /api/admin/freshness`
- Added `POST /api/admin/backfill/prediction-dashboard-metrics`

## Hosted Verification

- The hosted dashboard at `http://34.68.246.153:8000/dashboard` is still serving the older dashboard template.
- The hosted API at `http://34.68.246.153:8000/api/edges/today` is already returning persisted K-BB, park, and pythagorean metrics.
- Result: the deployed backend has the data, but the deployed UI still needs a code deploy to show the newer cards and deep-dive fallbacks.

## Follow-Up

- Run `alembic upgrade head`
- Redeploy the app so `/dashboard` serves the updated templates
- Use `/api/admin/freshness` after deploy to confirm today’s pipeline timestamps
- Use the backfill endpoint if newly added persisted dashboard fields need repopulation
