# MLB 2027 winter plan

Hobby research, with no promise of profit. Rules and targets below are proposals. Provider terms and the official Opening Day date remain unverified offline.

## What 2026 taught us

Report figures: [CALIBRATION_REPORT.md](CALIBRATION_REPORT.md).

- Jared's summary: 50.6% on 2,695 picks, -3.3% ROI, 32% beating the close. The report's separate paper ledger: 170 priced bets returned -1.3%; 38 lacked entry prices.
- Moneyline closing walk-forward scores favored the market: Brier/log loss 0.2460/0.6850 versus model 0.2483/0.6909, across 1,898 games.
- Totals were substantially worse: market 0.2498/0.6927 versus model 0.2700/0.7484 across 1,835 games. The 0–10% over bin went over 51.6% of the time (31 games).
- Joint-versus-fitted-market 90% score intervals all crossed zero. Adding the model has not demonstrated incremental information.
- Recalibration shrank mean hypothetical edge from 14.0% to 2.2%; only 341/1,785 recommendations survived. Surviving totals returned -6.8%; moneylines' +7.0% came from just 26 bets.
- Price history was inadequate: 1,055/1,096 decisions lacked price CLV; 116 prediction and 2,161 odds timestamps were not before start. Latest-pregame fallback is not a verified close.
- Starter xERA was missing in 19.9% of home and 20.5% of away rows. Season total SD was 4.51 runs versus the decision rule's fixed 2.5; unconditional spread is not conditional uncertainty.

## Model design for 2027

Start with a same-book, two-sided no-vig probability; let baseball information adjust it. Keep raw-market and fitted-market baselines, including zero model weight. Existing `joint-monthly-c1-v1` shadow fits already support that fallback. Legacy weekly refitting still uses 2022–24, explaining why it did not learn from 2026.

Refit monthly on the first, freeze that month's fit, and use forecasts, games, and observed results before a one-day boundary embargo. Earlier-month inner validation chooses model contribution. Save cutoffs and versions. This already exists in shadow.

Use confirmed batting orders and starting pitchers, with availability timestamps and prior-game-only statistics. At 45 minutes before scheduled first pitch, decide once, only if confirmations exist and quotes are at most ten minutes old; otherwise skip. A subsequent scratch cancels eligibility and records the reason. Existing scheduling uses 45 minutes; confirmed-lineup inputs and mandatory refresh still need work.

Replace fixed-Normal totals with conditional count probabilities and explicit push mass. Negative-binomial monthly candidates already exist with and without the pre-June ×0.82 factor. Remove the factor unless chronological ablation supports it; avoid scaling means after winner probabilities are computed. Market-anchor the winning totals candidate before any promotion; current count candidates are separate shadow forecasts.

## Markets and affordable collection

Prioritize moneylines and totals. Batter hits/total bases need lineup position and plate-appearance estimates; pitcher strikeouts need opponent strikeout rates, workload and removal risk.

Budget against the task's shared free 500 credits/month. Planning assumption: one-region game boards cost one credit per market per request; props cost one per event per market per snapshot. Confirm terms before winter collection.

| Monthly allowance | Credits | Practical limit |
| --- | ---: | --- |
| NHL/NBA | 250 | Shared reserve |
| MLB | 200 | Game lines first |
| Contingency | 50 | Retries/extra closing captures |

Two daily moneyline/totals board requests cost about 120 credits in 30 days. Staggered starts require more fresh entry/close requests; restrict the cohort by a frozen schedule. The remaining 80 fund at most 40 entry/close pairs for one prop market, or 13 for all three. Three across 15 games/day with two snapshots cost 2,700/month. A small strikeout pilot is affordable; broad props coverage requires paid data. Stop at the cap.

## Winter data work

**Data-source decision (Jared, 2026-10-25).** MLB.com's Terms of Use (Section 1, item xi, updated March 11, 2025) prohibit using automated scripts to collect information from MLB Digital Properties, which covers the Stats API and Baseball Savant. Jared chose to keep the daily pipeline's existing low-volume Stats API use as is, as a hobby, and to do **no bulk historical backfill**. Winter work uses only the owned export of the 2026 database (`mlb-export-20261025`, 31 tables, taken read-only on 2026-10-25) and the N1 inventory built from it (TKT-0015).

The December dataset is therefore built from exported rows only: prior-day team, pitcher and bullpen features come from what the database already stored (`games`, `predictions`, `forecast_history`, `reliever_workload`, `umpire_assignments_v4`, `game_odds`). Fields the export lacks stay missing and are labeled; they are not fetched. N1 found every game has a pre-start prediction and 99.25% have final scores, both starter xERA values exist for 70.3% of games, a known umpire before first pitch for 9%, and lineups for 0%.

Export available 2026 `forecast_history`, `price_history`, `decision_history`, fits and grades. Preserve feature hashes, source/receipt times, starts, book, line, both prices and rejected decisions. October append-only history cannot recreate earlier missing snapshots. Keep legacy exports separate; never invent closes or lineup announcement times.

Owned exports are free. Historical intraday odds/props and reliable timestamped lineup archives may require paid providers; Odds API historical access is paid. Purchase nothing without a separate budget decision. Produce a coverage/missingness manifest and deterministic as-of replay. November's manifest is done (TKT-0015: 0 verified closes in 2026, so 2026 is development data only).

## Rules lock

Before Opening Day, freeze features, distributions, refit schedule/window, selection algorithm, timing, freshness, books, cohort selection, edge/EV thresholds, stake limits, settlement, quota and gate rules. Monthly fitted coefficients may change only through that frozen procedure. Paper-only is the default.

Change design between seasons, or register a new shadow version and untouched prospective cohort before results arrive. Require a market-specific gate and Jared's explicit promotion approval; never reuse a failed cohort. Current gate enrolls only the joint candidate: 300 non-push games, at least ten dates, both score-difference one-sided 95% upper bounds below zero, full verified CLV coverage and positive mean CLV. Totals count models and props need separate gates.

## Milestones

| Date | Deliverable | Done means |
| --- | --- | --- |
| November 2026 | Data inventory and credit budget | Coverage manifest identifies unrecoverable history; shared allocations total 500 |
| December 2026 | Point-in-time dataset from the 2026 export only (no backfill) | As-of audit finds no future inputs; every feature traced to an exported column and timestamp; missing fields labeled, not fetched; uncertain lineage excluded |
| January 2027 | Chronological model comparison | Baselines, count distributions and 0.82 ablation replay reproducibly; 2026 labeled development data |
| February 2027 | Confirmation and collection rehearsal | Scratches, missing lineups, restart, pushes and quota exhaustion produce recorded skips/settlements |
| March 2027, before Opening Day | Signed rules and version manifest | Independent replay matches saved decisions; candidate/gate and quota locked |
| Opening Day 2027 | Start untouched paper cohort | First eligible forecast preserves inputs, price, policy and fit cutoff |

## Success criteria for 2027

Proposed targets per fixed 300-game market cohort:

| Market | Mean no-vig CLV | Candidate minus market Brier / log loss |
| --- | --- | --- |
| Moneyline | ≥+0.5 percentage points | ≤-0.001 / ≤-0.002 |
| Game totals | ≥+0.5 points | ≤-0.001 / ≤-0.002 |
| Batter hits | ≥+0.5 points | ≤-0.001 / ≤-0.002 |
| Batter total bases | ≥+0.5 points | ≤-0.001 / ≤-0.002 |
| Pitcher strikeouts | ≥+0.5 points | ≤-0.001 / ≤-0.002 |

Require both score improvements against raw and fitted-market baselines, date-cluster one-sided 95% upper bounds below zero, and CLV lower bound above zero. Use at least ten dates, one prespecified observation per game/market, full same-book/same-line verified coverage, and a close within ten minutes of first pitch. Score binary outcomes excluding pushes; retain push mass and zero-profit settlements. Freeze sides before outcomes. Account for multiple candidate trials before promotion. Report after-vig ROI separately.

Passing supports an edge, without guaranteeing profit. Failed targets mean no demonstrated edge; insufficient coverage/sample means inconclusive. Failure or uncertainty means paper-only hobby.

## What not to do

Do not tune rules on the season being judged, change thresholds midseason, repeatedly test until passing, chase profitable subgroups, substitute OCR-like silent pitcher defaults, fill unknown odds with -110, treat missing CLV as zero, use closes as entry features, or let profitable backtests unlock trading.

Basis: [IMPROVEMENT_IDEAS.md](IMPROVEMENT_IDEAS.md), task-listed code and its history, shadow-math and verified-CLV helpers.
