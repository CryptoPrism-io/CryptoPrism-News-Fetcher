# ML Pipeline Overhaul — April 8-9, 2026

## Summary

Major fixes to the CryptoPrism ML signal pipeline. The model was training on 100% NULL features due to a database routing bug. After fixing, model quality improved from negative IC to Sharpe 6.18.

---

## Critical Fix: Training Was Blind (41/54 Features = 0%)

### Problem
`train_lgbm.py` read features from `mv_ml_feature_matrix` on **dbcp**, where all FE tables (PCT_CHANGE, MOMENTUM, OSCILLATORS, TVV, RATIOS) contain only a 1-day snapshot (today's data). The materialized view LEFT JOINed these tables, so every historical row had NULL for all price/technical features.

The model was effectively training on `fear_greed_index` alone — the only feature with historical data on dbcp.

### Root Cause
- **dbcp** FE tables: overwritten daily by CryptoPrism-DB (TRUNCATE + INSERT), only 1 date
- **cp_backtest** FE tables: full historical data (millions of rows, years of history)
- Training code used `get_db_conn()` (dbcp) instead of `get_backtest_conn()` (cp_backtest)

### Fix (commit `f062373`)
Implemented dual-DB training in `train_lgbm.py`:
- **Labels**: ML_LABELS from dbcp (unchanged)
- **Price features**: FE_PCT_CHANGE, FE_MOMENTUM_SIGNALS, FE_OSCILLATORS_SIGNALS, FE_TVV_SIGNALS, FE_RATIOS_SIGNALS from cp_backtest with `DISTINCT ON` dedup
- **Supplementary**: FE_FEAR_GREED_CMC, FE_NEWS_SIGNALS from dbcp
- Falls back to MV approach when `DB_BACKTEST_NAME` is not set

### Result
| Metric | Before | After |
|--------|--------|-------|
| Features with data | 1/54 | **46/46** |
| Test IC-3d | -0.007 | **+0.081** |
| Test Sharpe | -2.16 | **+6.18** |
| Test MaxDD | -20.9% | **-7.7%** |
| Z-score signals | 0 BUY, 1 SELL | **98 BUY, 786 SELL** |

---

## Fix: Weekly Retrain Crashed Every Sunday

### Problem
`daily-ml-signals.yml` weekly retrain (Sunday 02:00 UTC) called `labels.py` without `--from-date`, generating labels for yesterday only. The rolling walk-forward splits needed months of labels for val/test windows, but those rows had NULL `label_3d` (forward prices weren't known when computed). Result: `KeyError: 'label_3d'` on empty DataFrame every Sunday since March 15.

### Fix (commit `3446e75`)
- Weekly retrain now passes `--from-date $(date -d "60 days ago")` to `labels.py`
- Added explicit guard in `train_lgbm.py` for empty val/test sets with a clear error message
- Backfilled 44,999 ML_LABELS rows (Feb 7 - Mar 24) to close the gap

---

## Fix: Hourly News Fetch Failed on Empty API Response (Fixed Earlier)

### Problem
CryptoCompare API returns 0 articles during low-activity hours. Code used `exit(1)`, failing the workflow.

### Fix (commit `c363ae8`, March 12)
Changed to `exit(0)` — no failures since.

---

## Fix: NLP Pipeline sslmode Error (Fixed Earlier)

### Problem
`psycopg2.OperationalError: invalid sslmode value: ""` when `DB_SSLMODE` secret was empty string.

### Fix
Bash conditional `[ -n "$SSLMODE" ] && echo "DB_SSLMODE=$SSLMODE" >> .env || true` — no failures since Feb 25.

---

## Cleanup: Removed Dead Features

### Change (commit `fa0183c`)
Removed FE_METRICS_SIGNAL (5 features) and FE_DMV_SCORES (3 features) from the feature set. These tables have no historical data on cp_backtest and were always NaN. Clean 46/46 feature fill rate.

---

## Local .env Fix

Added `DB_BACKTEST_NAME=cp_backtest` to local `.env`. Without this, `get_backtest_conn()` silently fell back to dbcp, making all local runs hit the wrong database.

---

## Database State Reference

### cp_backtest (training source)
| Table | Rows | Dates | Range |
|-------|------|-------|-------|
| FE_PCT_CHANGE | 2,392,511 | 4,709 | 2013-04-28 to 2026-04-07 |
| FE_OSCILLATOR | 2,395,089 | 4,707 | 2013-04-28 to 2026-04-07 |
| FE_TVV | 2,396,088 | 4,707 | 2013-04-28 to 2026-04-07 |
| FE_MOMENTUM | 453,483 | 394 | 2025-02-17 to 2026-04-07 |
| FE_RATIOS | 704,264 | 578 | 2024-08-24 to 2026-04-07 |
| 1K_coins_ohlcv | 2,473,322 | 4,722 | 2013-04-28 to 2026-04-07 |

### dbcp (supplementary + writes)
| Table | Rows | Dates |
|-------|------|-------|
| FE_FEAR_GREED_CMC | 1,014 | 1,014 |
| FE_NEWS_SIGNALS | 6,593 | 170 |
| ML_LABELS | 155,894 | 156 |
| ML_SIGNALS | 61,789+ | 63+ |

### Known Issues (deferred)
- cp_backtest FE tables have duplicate (slug, date) rows from repeated appends — handled with `DISTINCT ON` in queries
- Daily signals_only cron (01:00 UTC) often finds no features because FE tables refresh later — needs cron timing adjustment
- FE_MOMENTUM raw table was wiped during backfill (FE_MOMENTUM_SIGNALS still intact)

---

## Commits
| Hash | Message |
|------|---------|
| `3446e75` | fix: weekly retrain backfills labels for full training window |
| `f062373` | feat: dual-DB training reads features from cp_backtest |
| `fa0183c` | refactor: remove FE_METRICS_SIGNAL and FE_DMV_SCORES from feature set |
