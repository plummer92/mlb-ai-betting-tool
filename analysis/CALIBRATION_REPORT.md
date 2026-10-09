# Calibration report

This export does not establish a dependable betting edge. Of 208 recorded paper bets, 170 have recorded or safely recovered entry odds; their return was -1.3% (game-cluster bootstrap 90% interval -15.4% to 12.8%). The other 38 lack verifiable entry prices, so whole-ledger ROI is not established. The tables below distinguish full-season descriptions, monthly out-of-sample forecasts, and hypothetical bets. Recalibration is a research result only; no live decision probabilities or eligibility rules were changed.

moneyline: joint minus fitted-market Brier +0.000050, log loss +0.000102; totals: joint minus fitted-market Brier -0.000157, log loss +0.000890. Negative differences favor adding the model. All joint-versus-fitted-market 90% score-difference intervals cross zero; incremental information is not established.

Totals are overconfident: the 0-10% over-probability bin actually went over 51.6% of the time (31 games). Claimed edge is not usefully monotonic with returns: priced-paper Spearman rho is -0.0099 (naive p=0.898). Recalibration reduced mean hypothetical OOS edge from 14.0% to 2.2%; 341 of 1,785 recommendations survived. Their moneyline ROI was +7.0% (26 bets; 90% interval -30.8% to +46.0%), and totals ROI was -6.8% (315; -15.3% to +1.9%). Of 61 reconstructable recorded paper bets with an earlier-month fit, 37 totals and zero moneylines survived; those totals returned +17.8% (-7.4% to +42.9%). This small early-season subset does not establish profit.

## Method and limits

- Run `python analysis/season_calibration.py` from the repository root. Inputs are UTF-8 CSVs; no credentials, app imports, databases or network. Output hashes are in `data_audit.json`. Bootstrap seed 20261009, 3,000 resamples.
- Final outcomes come from final-status games with complete nonzero scores, checked against all available outcome reviews. Do not restrict outcomes to reviewed predictions: that would reproduce the settlement selection bias.
- **Closing cohort:** one latest prediction timestamp strictly before scheduled start per game; one latest valid odds row strictly before start per market (timestamp, sportsbook, then ID tie-break). There are no true `close` rows, so this is latest-pregame/open fallback. Home-win and over are the fixed scoring event orientations. Reliability also shows under as the complement; its Brier/log loss are identical to over, so the two sides are not pooled as independent observations. Totals probabilities reconstruct the live Normal(mean=projected total, SD=2.5) rule at the selected market line. Binary scoring excludes pushes; ROI includes them with zero profit.
- Moneyline `model_p` uses stored Platt-calibrated probability when present, otherwise stored raw probability. `raw_p` is also reported. A fixed 50/50 arithmetic blend is descriptive; it is not optimized on the season. No-vig probability is one side's American implied probability divided by the sum of both sides from the same book and snapshot. Brier/log loss use the identical matched sample for each comparison, with probabilities clipped to [0.000001, 0.999999].
- **Walk-forward:** train expanding earlier months, test the next month, with a one-day boundary embargo; minimum 30 training games and both outcomes. Three fixed logistic fits use logit(market), both logit(market) and logit(model), or logit(model) alone. C=1 L2 regularization and the survival rule are fixed in this script; no held-out hyperparameter search. Coefficients and month-specific metrics are exported. Joint versus fitted-market is the incremental-information comparison; raw market remains a separate baseline. Closing fits are diagnostic and cannot price an earlier trade.
- **Decision cohort and recalibration:** train separately on one latest eligible pregame edge per game, using its stored same-book prices and model probabilities. Legacy missing prices use linked odds only when fetched no later than the edge. Post-start edges and future fallback prices are excluded. Fit using previous months only, then apply the joint fit to each recorded/hypothetical entry's own line and prices. Keep its original recommended side only if recalibrated probability edge >=4 percentage points and EV >=3%. This fixed research threshold does not reproduce the current live policy or claim a new optimal policy. No side switching or outcome-based bet selection.
- Paper outcomes use **all 208 recorded paper trades**, including unresolved rows regraded offline. ROI includes only recorded odds or exact linked same-game/line prices fetched by `BetAlert.alert_time`: historical `PaperTrade.placed_at` often records May mirror creation, not entry. Unknown prices are excluded for both wins and losses, with unpriced counts reported; missing odds are never replaced with -110 or a later close. This priced subset can have selection bias and is not whole-ledger ROI. These are simulated stakes, not actual cash fills. Candidate ROI is a **hypothetical replay** at exported edge prices, one selected recommendation per game; candidates were not placed and executability is unproven. Paper recalibration additionally excludes entries whose mutable edge/opposite price cannot be reconstructed by alert time. The export omits `bet_executions` and bankroll snapshots; order-only prices are requests, not independently verified fills.
- The export is a current snapshot, not an immutable history. Prediction/odds/edge upserts can change values without a full event log; scheduled starts can change. Timestamp filters reduce leakage but cannot prove original feature availability. Historical calibration parameter training data and lineup/stat snapshots are not exported. Monthly OOS here means the **new** logistic fit saw only earlier outcomes; it does not certify that the original live model's features or policy were never retrospectively selected.
- All full-season edge buckets, reliability summaries and correlations are descriptive/in-sample. The existing tightened policy was selected using historical profitability; a reported +6.4% from that selection is **in-sample**, not evidence of future profit. This study does not optimize or deploy that policy. Bootstrap intervals reflect sampling uncertainty conditional on the saved cohort, not all model-selection/data-history uncertainty. Spearman p-values are naive diagnostics and do not adjust game dependence or multiple comparisons.

## Coverage and score metrics

| item | value |
| --- | --- |
| source_rows | {"games": 2388, "predictions": 4116, "edge_results": 3216, "game_outcomes_review": 3506, "paper_trades": 208, "bet_alerts": 208, "game_odds": 18876, "line_movement": 1769, "tradable_decision_snapshots": 1096, "bet_orders": 5, "sandbox_predictions_v4": 2135} |
| review_score_conflicts | 0 |
| predictions_not_before_start_final_games | 116 |
| odds_not_before_start_final_games | 2161 |
| closing_games_by_market | {"moneyline": 1969, "totals": 1903} |
| stored_edge_price_rows | 2527 |
| edge_price_asof_fallback_rows | 428 |
| edge_price_missing_or_future_fallback_rows | 164 |
| paper_entry_reconstructable | 164 |
| paper_price_source | {"linked_snapshot_asof": 159, "unavailable": 38, "paper_recorded": 11} |
| paper_recorded_result_conflicts | 0 |
| paper_alert_unresolved | 168 |
| paper_persisted_open | 172 |
| paper_games_with_reviews | 51 |
| paper_predictions_with_reviews | 40 |
| decision_status_counts | {"BLOCKED": 1029, "WATCH": 67} |
| tradable_signal_counts | {"WATCH": 789, "PASS": 307} |
| tradable_trade_allowed | 0 |
| missing_xera_fraction | {"home_starter_xera": 0.1987366375121477, "away_starter_xera": 0.20481049562682216} |
| recorded_paper_overall | {"n": 170, "unpriced": 38, "games": 118, "roi": -0.013442249193123493, "low90": -0.15407193922395138, "high90": 0.12784332925052816} |
| recorded_paper_results | {"win": 108, "loss": 94, "push": 6} |
| actual_total_mean | 8.9755 |
| actual_total_std | 4.5052 |
| snapshots_missing_price_clv | 1055 |
| order_score_results | {"win": 3, "loss": 2} |

| cohort | market | forecast | n | brier | log_loss |
| --- | --- | --- | --- | --- | --- |
| full_season_descriptive | moneyline | raw_p | 1969 | 0.2483 | 0.6928 |
| full_season_descriptive | moneyline | model_p | 1969 | 0.2494 | 0.6952 |
| full_season_descriptive | moneyline | market_p | 1969 | 0.2459 | 0.6848 |
| full_season_descriptive | moneyline | blend | 1969 | 0.2464 | 0.6862 |
| full_season_descriptive | totals | raw_p | 1903 | 0.2691 | 0.7463 |
| full_season_descriptive | totals | model_p | 1903 | 0.2691 | 0.7463 |
| full_season_descriptive | totals | market_p | 1903 | 0.2498 | 0.6927 |
| full_season_descriptive | totals | blend | 1903 | 0.2529 | 0.6995 |
| closing_walk_forward | moneyline | model_p | 1898 | 0.2483 | 0.6909 |
| closing_walk_forward | moneyline | market_p | 1898 | 0.2460 | 0.6850 |
| closing_walk_forward | moneyline | blend | 1898 | 0.2461 | 0.6855 |
| closing_walk_forward | moneyline | market_fit | 1898 | 0.2461 | 0.6852 |
| closing_walk_forward | moneyline | joint | 1898 | 0.2461 | 0.6853 |
| closing_walk_forward | moneyline | platt | 1898 | 0.2493 | 0.6918 |
| closing_walk_forward | totals | model_p | 1835 | 0.2700 | 0.7484 |
| closing_walk_forward | totals | market_p | 1835 | 0.2498 | 0.6927 |
| closing_walk_forward | totals | blend | 1835 | 0.2533 | 0.7003 |
| closing_walk_forward | totals | market_fit | 1835 | 0.2525 | 0.6982 |
| closing_walk_forward | totals | joint | 1835 | 0.2524 | 0.6991 |
| closing_walk_forward | totals | platt | 1835 | 0.2522 | 0.6987 |
| decision_walk_forward | moneyline | model_p | 1785 | 0.2487 | 0.6918 |
| decision_walk_forward | moneyline | market_p | 1785 | 0.2461 | 0.6852 |
| decision_walk_forward | moneyline | blend | 1785 | 0.2463 | 0.6860 |
| decision_walk_forward | moneyline | market_fit | 1785 | 0.2461 | 0.6852 |
| decision_walk_forward | moneyline | joint | 1785 | 0.2460 | 0.6852 |
| decision_walk_forward | moneyline | platt | 1785 | 0.2496 | 0.6923 |
| decision_walk_forward | totals | model_p | 1724 | 0.2707 | 0.7473 |
| decision_walk_forward | totals | market_p | 1724 | 0.2496 | 0.6923 |
| decision_walk_forward | totals | blend | 1724 | 0.2534 | 0.7004 |
| decision_walk_forward | totals | market_fit | 1724 | 0.2526 | 0.6984 |
| decision_walk_forward | totals | joint | 1724 | 0.2518 | 0.6970 |
| decision_walk_forward | totals | platt | 1724 | 0.2516 | 0.6966 |

## Paired OOS score differences (negative favors first forecast)

Intervals resample games with frozen OOS predictions; they do not refit models or capture training/model-selection uncertainty.

| cohort | market | comparison | metric | n | mean_difference | low90 | high90 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| closing_walk_forward | moneyline | model_p minus market_p | brier | 1898 | 0.0023 | -0.0000 | 0.0047 |
| closing_walk_forward | moneyline | model_p minus market_p | log_loss | 1898 | 0.0059 | 0.0007 | 0.0113 |
| closing_walk_forward | moneyline | joint minus market_p | brier | 1898 | 0.0001 | -0.0013 | 0.0016 |
| closing_walk_forward | moneyline | joint minus market_p | log_loss | 1898 | 0.0003 | -0.0027 | 0.0034 |
| closing_walk_forward | moneyline | joint minus market_fit | brier | 1898 | 0.0000 | -0.0002 | 0.0003 |
| closing_walk_forward | moneyline | joint minus market_fit | log_loss | 1898 | 0.0001 | -0.0004 | 0.0006 |
| closing_walk_forward | totals | model_p minus market_p | brier | 1835 | 0.0202 | 0.0138 | 0.0272 |
| closing_walk_forward | totals | model_p minus market_p | log_loss | 1835 | 0.0557 | 0.0396 | 0.0733 |
| closing_walk_forward | totals | joint minus market_p | brier | 1835 | 0.0026 | 0.0008 | 0.0044 |
| closing_walk_forward | totals | joint minus market_p | log_loss | 1835 | 0.0064 | 0.0020 | 0.0110 |
| closing_walk_forward | totals | joint minus market_fit | brier | 1835 | -0.0002 | -0.0022 | 0.0020 |
| closing_walk_forward | totals | joint minus market_fit | log_loss | 1835 | 0.0009 | -0.0038 | 0.0060 |
| decision_walk_forward | moneyline | model_p minus market_p | brier | 1785 | 0.0026 | 0.0002 | 0.0051 |
| decision_walk_forward | moneyline | model_p minus market_p | log_loss | 1785 | 0.0066 | 0.0012 | 0.0121 |
| decision_walk_forward | moneyline | joint minus market_p | brier | 1785 | -0.0000 | -0.0014 | 0.0013 |
| decision_walk_forward | moneyline | joint minus market_p | log_loss | 1785 | -0.0001 | -0.0029 | 0.0028 |
| decision_walk_forward | moneyline | joint minus market_fit | brier | 1785 | -0.0000 | -0.0003 | 0.0002 |
| decision_walk_forward | moneyline | joint minus market_fit | log_loss | 1785 | -0.0000 | -0.0006 | 0.0005 |
| decision_walk_forward | totals | model_p minus market_p | brier | 1724 | 0.0211 | 0.0144 | 0.0277 |
| decision_walk_forward | totals | model_p minus market_p | log_loss | 1724 | 0.0549 | 0.0390 | 0.0706 |
| decision_walk_forward | totals | joint minus market_p | brier | 1724 | 0.0022 | 0.0005 | 0.0039 |
| decision_walk_forward | totals | joint minus market_p | log_loss | 1724 | 0.0047 | 0.0011 | 0.0083 |
| decision_walk_forward | totals | joint minus market_fit | brier | 1724 | -0.0008 | -0.0029 | 0.0013 |
| decision_walk_forward | totals | joint minus market_fit | log_loss | 1724 | -0.0014 | -0.0056 | 0.0029 |

## Reliability: fixed 10-point probability bins

| market | forecast | bin | n | mean_probability | actual_frequency |
| --- | --- | --- | --- | --- | --- |
| moneyline_home | model_p | [0.0, 0.1) | 1 | 10.0% | 100.0% |
| moneyline_home | model_p | [0.1, 0.2) | 3 | 16.9% | 66.7% |
| moneyline_home | model_p | [0.2, 0.3) | 5 | 27.2% | 80.0% |
| moneyline_home | model_p | [0.3, 0.4) | 12 | 35.7% | 33.3% |
| moneyline_home | model_p | [0.4, 0.5) | 535 | 48.5% | 45.0% |
| moneyline_home | model_p | [0.5, 0.6) | 1363 | 52.1% | 54.7% |
| moneyline_home | model_p | [0.6, 0.7) | 13 | 65.5% | 69.2% |
| moneyline_home | model_p | [0.7, 0.8) | 15 | 76.6% | 66.7% |
| moneyline_home | model_p | [0.8, 0.9) | 10 | 85.2% | 60.0% |
| moneyline_home | model_p | [0.9, 1.0) | 12 | 93.7% | 50.0% |
| totals_over | model_p | [0.0, 0.1) | 31 | 5.5% | 51.6% |
| totals_over | model_p | [0.1, 0.2) | 89 | 15.5% | 53.9% |
| totals_over | model_p | [0.2, 0.3) | 167 | 25.2% | 46.1% |
| totals_over | model_p | [0.3, 0.4) | 286 | 35.2% | 45.1% |
| totals_over | model_p | [0.4, 0.5) | 402 | 45.3% | 53.5% |
| totals_over | model_p | [0.5, 0.6) | 450 | 54.6% | 47.6% |
| totals_over | model_p | [0.6, 0.7) | 364 | 64.5% | 52.7% |
| totals_over | model_p | [0.7, 0.8) | 104 | 73.4% | 57.7% |
| totals_over | model_p | [0.8, 0.9) | 10 | 82.8% | 100.0% |
| totals_under | model_p | [0.1, 0.2) | 10 | 17.2% | 0.0% |
| totals_under | model_p | [0.2, 0.3) | 104 | 26.6% | 42.3% |
| totals_under | model_p | [0.3, 0.4) | 364 | 35.5% | 47.3% |
| totals_under | model_p | [0.4, 0.5) | 441 | 45.3% | 52.4% |
| totals_under | model_p | [0.5, 0.6) | 411 | 54.6% | 46.7% |
| totals_under | model_p | [0.6, 0.7) | 286 | 64.8% | 54.9% |
| totals_under | model_p | [0.7, 0.8) | 167 | 74.8% | 53.9% |
| totals_under | model_p | [0.8, 0.9) | 89 | 84.5% | 46.1% |
| totals_under | model_p | [0.9, 1.0) | 31 | 94.5% | 48.4% |

## Claimed edges versus realized ROI

| cohort | market | edge_bucket | n | unpriced | games | roi | low90 | high90 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| recorded_paper | all | 0-5% | 3 | 1 | 3 | 41.4% | -34.9% | 117.7% |
| recorded_paper | all | 5-10% | 16 | 2 | 13 | -5.2% | -46.9% | 35.8% |
| recorded_paper | all | 10-15% | 18 | 1 | 15 | -1.3% | -39.1% | 37.8% |
| recorded_paper | all | 15-20% | 27 | 4 | 20 | 11.2% | -26.9% | 51.8% |
| recorded_paper | all | 20-30% | 72 | 24 | 56 | -5.2% | -26.0% | 16.6% |
| recorded_paper | all | 30%+ | 34 | 6 | 21 | -5.2% | -38.0% | 27.7% |
| recorded_paper | moneyline | 0-5% | 2 | 1 | 2 | 112.1% | 95.2% | 129.0% |
| recorded_paper | moneyline | 5-10% | 9 | 1 | 8 | 25.8% | -28.0% | 73.1% |
| recorded_paper | moneyline | 10-15% | 14 | 1 | 12 | -14.2% | -61.0% | 25.2% |
| recorded_paper | moneyline | 15-20% | 12 | 2 | 8 | 0.8% | -63.0% | 69.2% |
| recorded_paper | moneyline | 20-30% | 33 | 0 | 22 | -23.8% | -53.7% | 7.6% |
| recorded_paper | moneyline | 30%+ | 14 | 1 | 8 | -43.2% | -88.2% | 6.5% |
| recorded_paper | totals | 0-5% | 1 | 0 | 1 | -100.0% | -100.0% | -100.0% |
| recorded_paper | totals | 5-10% | 7 | 1 | 6 | -45.0% | -100.0% | 25.7% |
| recorded_paper | totals | 10-15% | 4 | 0 | 4 | 43.7% | -51.2% | 93.3% |
| recorded_paper | totals | 15-20% | 15 | 2 | 12 | 19.6% | -25.8% | 67.2% |
| recorded_paper | totals | 20-30% | 39 | 24 | 34 | 10.6% | -17.0% | 37.3% |
| recorded_paper | totals | 30%+ | 20 | 5 | 13 | 21.4% | -21.4% | 60.2% |
| hypothetical_latest_edge | all | 0-5% | 234 | 0 | 234 | -25.0% | -34.8% | -14.9% |
| hypothetical_latest_edge | all | 5-10% | 525 | 0 | 525 | -1.6% | -8.4% | 5.5% |
| hypothetical_latest_edge | all | 10-15% | 373 | 0 | 373 | 1.1% | -6.9% | 9.1% |
| hypothetical_latest_edge | all | 15-20% | 287 | 0 | 287 | 5.1% | -4.1% | 14.3% |
| hypothetical_latest_edge | all | 20-30% | 310 | 0 | 310 | -1.8% | -10.2% | 6.5% |
| hypothetical_latest_edge | all | 30%+ | 126 | 0 | 126 | -3.4% | -16.6% | 10.4% |
| hypothetical_latest_edge | moneyline | 0-5% | 36 | 0 | 36 | -54.0% | -77.2% | -30.5% |
| hypothetical_latest_edge | moneyline | 5-10% | 41 | 0 | 41 | 34.1% | 5.1% | 61.8% |
| hypothetical_latest_edge | moneyline | 10-15% | 21 | 0 | 21 | -10.2% | -49.5% | 30.4% |
| hypothetical_latest_edge | moneyline | 15-20% | 13 | 0 | 13 | -15.4% | -61.9% | 37.8% |
| hypothetical_latest_edge | moneyline | 20-30% | 22 | 0 | 22 | -22.6% | -52.0% | 9.2% |
| hypothetical_latest_edge | moneyline | 30%+ | 7 | 0 | 7 | -32.6% | -78.5% | 13.3% |
| hypothetical_latest_edge | totals | 0-5% | 198 | 0 | 198 | -19.7% | -30.7% | -8.5% |
| hypothetical_latest_edge | totals | 5-10% | 484 | 0 | 484 | -4.6% | -11.7% | 2.7% |
| hypothetical_latest_edge | totals | 10-15% | 352 | 0 | 352 | 1.8% | -6.6% | 10.0% |
| hypothetical_latest_edge | totals | 15-20% | 274 | 0 | 274 | 6.1% | -3.5% | 15.4% |
| hypothetical_latest_edge | totals | 20-30% | 288 | 0 | 288 | -0.3% | -9.5% | 8.6% |
| hypothetical_latest_edge | totals | 30%+ | 119 | 0 | 119 | -1.7% | -16.0% | 12.5% |

| cohort | market | n | spearman_rho | naive_p_value |
| --- | --- | --- | --- | --- |
| recorded_paper | all | 170 | -0.0099 | 0.8984 |
| recorded_paper | moneyline | 84 | -0.2065 | 0.0595 |
| recorded_paper | totals | 86 | 0.1239 | 0.2558 |
| hypothetical_latest_edge | all | 1855 | 0.0319 | 0.1696 |
| hypothetical_latest_edge | moneyline | 140 | 0.0028 | 0.9742 |
| hypothetical_latest_edge | totals | 1715 | 0.0464 | 0.0547 |

## Walk-forward recalibration and surviving bets

| cohort | market | selection | n | unpriced | games | roi | low90 | high90 | mean_original_edge | mean_recalibrated_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hypothetical_latest_edge | moneyline | original_OOS_bets | 97 | 0 | 97 | -15.2% | -33.8% | 2.8% | 8.7% | 1.8% |
| hypothetical_latest_edge | moneyline | recalibrated_survivors | 26 | 0 | 26 | 7.0% | -30.8% | 46.0% | 8.0% | 6.6% |
| hypothetical_latest_edge | totals | original_OOS_bets | 1688 | 0 | 1688 | -2.6% | -6.3% | 1.2% | 14.4% | 2.2% |
| hypothetical_latest_edge | totals | recalibrated_survivors | 315 | 0 | 315 | -6.8% | -15.3% | 1.9% | 20.5% | 7.8% |
| recorded_paper | moneyline | original_OOS_bets | 21 | 0 | 11 | -37.4% | -79.2% | 4.5% | 22.7% | -6.7% |
| recorded_paper | moneyline | recalibrated_survivors | 0 | 0 | 0 | — | — | — | — | — |
| recorded_paper | totals | original_OOS_bets | 40 | 0 | 37 | 16.4% | -7.2% | 39.7% | 27.0% | 13.5% |
| recorded_paper | totals | recalibrated_survivors | 37 | 0 | 34 | 17.8% | -7.4% | 42.9% | 27.4% | 14.4% |

## Monthly joint-fit coefficients

| cohort | market | test_month | train_n | test_n | train_last_date | fit | intercept | market_coefficient | model_coefficient |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| closing_diagnostic | moneyline | 2026-04 | 58 | 373 | 2026-03-30 | joint | 0.1244 | 0.5465 | -0.1029 |
| closing_diagnostic | moneyline | 2026-05 | 435 | 324 | 2026-04-29 | joint | 0.1030 | 0.3132 | 0.0064 |
| closing_diagnostic | moneyline | 2026-06 | 768 | 360 | 2026-05-24 | joint | 0.0685 | 0.4606 | -0.0113 |
| closing_diagnostic | moneyline | 2026-07 | 1125 | 333 | 2026-06-27 | joint | 0.0445 | 0.5276 | -0.0230 |
| closing_diagnostic | moneyline | 2026-08 | 1456 | 218 | 2026-07-28 | joint | 0.0128 | 0.6800 | -0.0566 |
| closing_diagnostic | moneyline | 2026-09 | 1677 | 274 | 2026-08-16 | joint | 0.0135 | 0.6640 | -0.0621 |
| closing_diagnostic | moneyline | 2026-10 | 1949 | 16 | 2026-09-29 | joint | 0.0038 | 0.8088 | -0.0758 |
| closing_diagnostic | totals | 2026-04 | 55 | 357 | 2026-03-30 | joint | -0.0463 | 0.2080 | 0.5410 |
| closing_diagnostic | totals | 2026-05 | 417 | 309 | 2026-04-29 | joint | 0.1349 | 0.2717 | 0.0546 |
| closing_diagnostic | totals | 2026-06 | 734 | 349 | 2026-05-24 | joint | 0.0364 | 0.0015 | 0.1322 |
| closing_diagnostic | totals | 2026-07 | 1080 | 327 | 2026-06-27 | joint | 0.0247 | 0.5300 | 0.1191 |
| closing_diagnostic | totals | 2026-08 | 1405 | 210 | 2026-07-28 | joint | -0.0010 | 1.0816 | 0.1253 |
| closing_diagnostic | totals | 2026-09 | 1618 | 267 | 2026-08-16 | joint | -0.0092 | 0.8211 | 0.0861 |
| closing_diagnostic | totals | 2026-10 | 1883 | 16 | 2026-09-29 | joint | 0.0454 | 0.3679 | 0.1224 |
| decision_asof | moneyline | 2026-04 | 58 | 344 | 2026-03-30 | joint | 0.1214 | 0.5666 | -0.1077 |
| decision_asof | moneyline | 2026-05 | 406 | 316 | 2026-04-29 | joint | 0.0820 | 0.4330 | -0.0363 |
| decision_asof | moneyline | 2026-06 | 731 | 327 | 2026-05-24 | joint | 0.0570 | 0.5547 | -0.0591 |
| decision_asof | moneyline | 2026-07 | 1055 | 314 | 2026-06-27 | joint | 0.0266 | 0.5807 | -0.0584 |
| decision_asof | moneyline | 2026-08 | 1368 | 203 | 2026-07-28 | joint | 0.0020 | 0.7112 | -0.0895 |
| decision_asof | moneyline | 2026-09 | 1574 | 266 | 2026-08-16 | joint | 0.0024 | 0.6812 | -0.0900 |
| decision_asof | moneyline | 2026-10 | 1837 | 15 | 2026-09-29 | joint | -0.0074 | 0.8320 | -0.1108 |
| decision_asof | totals | 2026-04 | 56 | 331 | 2026-03-30 | joint | -0.0785 | 0.1769 | 0.5399 |
| decision_asof | totals | 2026-05 | 391 | 298 | 2026-04-29 | joint | 0.1013 | -0.3536 | 0.1313 |
| decision_asof | totals | 2026-06 | 697 | 317 | 2026-05-24 | joint | 0.0128 | -0.3968 | 0.1527 |
| decision_asof | totals | 2026-07 | 1011 | 309 | 2026-06-27 | joint | 0.0159 | 0.4348 | 0.1055 |
| decision_asof | totals | 2026-08 | 1319 | 195 | 2026-07-28 | joint | -0.0120 | 0.9730 | 0.1148 |
| decision_asof | totals | 2026-09 | 1517 | 259 | 2026-08-16 | joint | -0.0173 | 0.6720 | 0.0707 |
| decision_asof | totals | 2026-10 | 1773 | 15 | 2026-09-29 | joint | 0.0373 | 0.5611 | 0.1210 |

## Monthly out-of-sample metrics

| cohort | market | forecast | n | brier | log_loss |
| --- | --- | --- | --- | --- | --- |
| closing:2026-04 | moneyline | market_p | 373 | 0.2528 | 0.6994 |
| closing:2026-04 | moneyline | market_fit | 373 | 0.2489 | 0.6909 |
| closing:2026-04 | moneyline | model_p | 373 | 0.2525 | 0.7043 |
| closing:2026-04 | moneyline | joint | 373 | 0.2492 | 0.6916 |
| closing:2026-04 | totals | market_p | 357 | 0.2497 | 0.6926 |
| closing:2026-04 | totals | market_fit | 357 | 0.2568 | 0.7068 |
| closing:2026-04 | totals | model_p | 357 | 0.2698 | 0.7569 |
| closing:2026-04 | totals | joint | 357 | 0.2581 | 0.7156 |
| closing:2026-05 | moneyline | market_p | 324 | 0.2466 | 0.6862 |
| closing:2026-05 | moneyline | market_fit | 324 | 0.2477 | 0.6886 |
| closing:2026-05 | moneyline | model_p | 324 | 0.2478 | 0.6887 |
| closing:2026-05 | moneyline | joint | 324 | 0.2477 | 0.6886 |
| closing:2026-05 | totals | market_p | 309 | 0.2505 | 0.6941 |
| closing:2026-05 | totals | market_fit | 309 | 0.2552 | 0.7036 |
| closing:2026-05 | totals | model_p | 309 | 0.2798 | 0.7782 |
| closing:2026-05 | totals | joint | 309 | 0.2533 | 0.6998 |
| closing:2026-06 | moneyline | market_p | 360 | 0.2474 | 0.6878 |
| closing:2026-06 | moneyline | market_fit | 360 | 0.2475 | 0.6881 |
| closing:2026-06 | moneyline | model_p | 360 | 0.2484 | 0.6900 |
| closing:2026-06 | moneyline | joint | 360 | 0.2475 | 0.6881 |
| closing:2026-06 | totals | market_p | 349 | 0.2488 | 0.6907 |
| closing:2026-06 | totals | market_fit | 349 | 0.2499 | 0.6929 |
| closing:2026-06 | totals | model_p | 349 | 0.2707 | 0.7442 |
| closing:2026-06 | totals | joint | 349 | 0.2498 | 0.6926 |
| closing:2026-07 | moneyline | market_p | 333 | 0.2436 | 0.6801 |
| closing:2026-07 | moneyline | market_fit | 333 | 0.2456 | 0.6843 |
| closing:2026-07 | moneyline | model_p | 333 | 0.2480 | 0.6892 |
| closing:2026-07 | moneyline | joint | 333 | 0.2456 | 0.6842 |
| closing:2026-07 | totals | market_p | 327 | 0.2476 | 0.6884 |
| closing:2026-07 | totals | market_fit | 327 | 0.2484 | 0.6899 |
| closing:2026-07 | totals | model_p | 327 | 0.2582 | 0.7233 |
| closing:2026-07 | totals | joint | 327 | 0.2477 | 0.6885 |
| closing:2026-08 | moneyline | market_p | 218 | 0.2493 | 0.6916 |
| closing:2026-08 | moneyline | market_fit | 218 | 0.2477 | 0.6884 |
| closing:2026-08 | moneyline | model_p | 218 | 0.2491 | 0.6913 |
| closing:2026-08 | moneyline | joint | 218 | 0.2476 | 0.6883 |
| closing:2026-08 | totals | market_p | 210 | 0.2512 | 0.6955 |
| closing:2026-08 | totals | market_fit | 210 | 0.2516 | 0.6964 |
| closing:2026-08 | totals | model_p | 210 | 0.2818 | 0.7657 |
| closing:2026-08 | totals | joint | 210 | 0.2539 | 0.7010 |
| closing:2026-09 | moneyline | market_p | 274 | 0.2340 | 0.6604 |
| closing:2026-09 | moneyline | market_fit | 274 | 0.2375 | 0.6679 |
| closing:2026-09 | moneyline | model_p | 274 | 0.2428 | 0.6787 |
| closing:2026-09 | moneyline | joint | 274 | 0.2375 | 0.6677 |
| closing:2026-09 | totals | market_p | 267 | 0.2519 | 0.6970 |
| closing:2026-09 | totals | market_fit | 267 | 0.2531 | 0.6993 |
| closing:2026-09 | totals | model_p | 267 | 0.2572 | 0.7124 |
| closing:2026-09 | totals | joint | 267 | 0.2509 | 0.6950 |
| closing:2026-10 | moneyline | market_p | 16 | 0.2529 | 0.6994 |
| closing:2026-10 | moneyline | market_fit | 16 | 0.2511 | 0.6954 |
| closing:2026-10 | moneyline | model_p | 16 | 0.2466 | 0.6864 |
| closing:2026-10 | moneyline | joint | 16 | 0.2514 | 0.6961 |
| closing:2026-10 | totals | market_p | 16 | 0.2485 | 0.6901 |
| closing:2026-10 | totals | market_fit | 16 | 0.2517 | 0.6966 |
| closing:2026-10 | totals | model_p | 16 | 0.3707 | 0.9612 |
| closing:2026-10 | totals | joint | 16 | 0.2641 | 0.7214 |
| decision:2026-04 | moneyline | market_p | 344 | 0.2519 | 0.6974 |
| decision:2026-04 | moneyline | market_fit | 344 | 0.2486 | 0.6904 |
| decision:2026-04 | moneyline | model_p | 344 | 0.2537 | 0.7072 |
| decision:2026-04 | moneyline | joint | 344 | 0.2487 | 0.6907 |
| decision:2026-04 | totals | market_p | 331 | 0.2510 | 0.6951 |
| decision:2026-04 | totals | market_fit | 331 | 0.2577 | 0.7087 |
| decision:2026-04 | totals | model_p | 331 | 0.2679 | 0.7363 |
| decision:2026-04 | totals | joint | 331 | 0.2561 | 0.7065 |
| decision:2026-05 | moneyline | market_p | 316 | 0.2462 | 0.6854 |
| decision:2026-05 | moneyline | market_fit | 316 | 0.2470 | 0.6872 |
| decision:2026-05 | moneyline | model_p | 316 | 0.2480 | 0.6892 |
| decision:2026-05 | moneyline | joint | 316 | 0.2470 | 0.6870 |
| decision:2026-05 | totals | market_p | 298 | 0.2501 | 0.6934 |
| decision:2026-05 | totals | market_fit | 298 | 0.2545 | 0.7021 |
| decision:2026-05 | totals | model_p | 298 | 0.2817 | 0.7818 |
| decision:2026-05 | totals | joint | 298 | 0.2510 | 0.6951 |
| decision:2026-06 | moneyline | market_p | 327 | 0.2482 | 0.6895 |
| decision:2026-06 | moneyline | market_fit | 327 | 0.2479 | 0.6888 |
| decision:2026-06 | moneyline | model_p | 327 | 0.2486 | 0.6903 |
| decision:2026-06 | moneyline | joint | 327 | 0.2479 | 0.6888 |
| decision:2026-06 | totals | market_p | 317 | 0.2480 | 0.6892 |
| decision:2026-06 | totals | market_fit | 317 | 0.2511 | 0.6953 |
| decision:2026-06 | totals | model_p | 317 | 0.2762 | 0.7575 |
| decision:2026-06 | totals | joint | 317 | 0.2521 | 0.6973 |
| decision:2026-07 | moneyline | market_p | 314 | 0.2442 | 0.6815 |
| decision:2026-07 | moneyline | market_fit | 314 | 0.2457 | 0.6844 |
| decision:2026-07 | moneyline | model_p | 314 | 0.2482 | 0.6896 |
| decision:2026-07 | moneyline | joint | 314 | 0.2455 | 0.6842 |
| decision:2026-07 | totals | market_p | 309 | 0.2477 | 0.6885 |
| decision:2026-07 | totals | market_fit | 309 | 0.2486 | 0.6904 |
| decision:2026-07 | totals | model_p | 309 | 0.2587 | 0.7254 |
| decision:2026-07 | totals | joint | 309 | 0.2479 | 0.6890 |
| decision:2026-08 | moneyline | market_p | 203 | 0.2501 | 0.6934 |
| decision:2026-08 | moneyline | market_fit | 203 | 0.2484 | 0.6898 |
| decision:2026-08 | moneyline | model_p | 203 | 0.2492 | 0.6915 |
| decision:2026-08 | moneyline | joint | 203 | 0.2484 | 0.6898 |
| decision:2026-08 | totals | market_p | 195 | 0.2518 | 0.6967 |
| decision:2026-08 | totals | market_fit | 195 | 0.2521 | 0.6973 |
| decision:2026-08 | totals | model_p | 195 | 0.2862 | 0.7755 |
| decision:2026-08 | totals | joint | 195 | 0.2544 | 0.7020 |
| decision:2026-09 | moneyline | market_p | 266 | 0.2340 | 0.6602 |
| decision:2026-09 | moneyline | market_fit | 266 | 0.2375 | 0.6678 |
| decision:2026-09 | moneyline | model_p | 266 | 0.2432 | 0.6795 |
| decision:2026-09 | moneyline | joint | 266 | 0.2374 | 0.6675 |
| decision:2026-09 | totals | market_p | 259 | 0.2497 | 0.6926 |
| decision:2026-09 | totals | market_fit | 259 | 0.2511 | 0.6954 |
| decision:2026-09 | totals | model_p | 259 | 0.2510 | 0.7000 |
| decision:2026-09 | totals | joint | 259 | 0.2490 | 0.6911 |
| decision:2026-10 | moneyline | market_p | 15 | 0.2627 | 0.7197 |
| decision:2026-10 | moneyline | market_fit | 15 | 0.2584 | 0.7105 |
| decision:2026-10 | moneyline | model_p | 15 | 0.2496 | 0.6924 |
| decision:2026-10 | moneyline | joint | 15 | 0.2591 | 0.7120 |
| decision:2026-10 | totals | market_p | 15 | 0.2498 | 0.6928 |
| decision:2026-10 | totals | market_fit | 15 | 0.2517 | 0.6966 |
| decision:2026-10 | totals | model_p | 15 | 0.3809 | 0.9845 |
| decision:2026-10 | totals | joint | 15 | 0.2641 | 0.7214 |

Row-level inputs, probabilities, realized returns and survival flags are exported beside this report for independent reproduction.
