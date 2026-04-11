# WS6: Second-Order Residual Features — April 11, 2026

## Summary

Implemented Workstream 6 from the IC improvement plan — 8 second-order features derived from hourly BTC residuals. These capture residual momentum, mean-reversion, volatility regime shifts, autocorrelation structure, and conviction-weighted alpha that the raw residual decomposition misses.

## New Features

| Feature | Description | Signal Type |
|---------|-------------|-------------|
| `res_momentum_3d` | 72h trailing mean of hourly residuals | Trending alpha (short-term) |
| `res_momentum_7d` | 168h trailing mean of hourly residuals | Trending alpha (medium-term) |
| `res_momentum_14d` | 336h trailing mean of hourly residuals | Trending alpha (long-term) |
| `res_zscore_30d` | z-score of 24h mean residual vs 30d distribution | Mean-reversion signal |
| `res_vol_regime` | 7d residual std / 30d residual std | Breakout detector (>1 = expanding vol) |
| `res_autocorr_7d` | Lag-1 autocorrelation over 168h | Trending vs mean-reverting residuals |
| `res_autocorr_14d` | Lag-1 autocorrelation over 336h | Longer-horizon persistence |
| `res_volume_interaction` | mean(residual * volume_zscore) over 24h | Conviction-weighted alpha |

## Why These Features Matter

The raw BTC residual decomposition (WS0, April 9) strips BTC correlation so the model sees only idiosyncratic returns. But raw residuals are noisy hour-to-hour. WS6 extracts **structure** from that noise:

- **Momentum features** detect whether a coin's alpha is trending (persistent outperformance/underperformance vs BTC)
- **Z-score** detects mean-reversion opportunities (residual stretched far from its 30d norm)
- **Vol regime** flags regime shifts — a coin whose residual volatility is expanding may be about to break out
- **Autocorrelation** tells the model whether residuals are trending (positive AC) or mean-reverting (negative AC), informing horizon selection
- **Volume interaction** weights the residual by conviction — a large residual on high volume is more meaningful than on thin volume

## Database Changes

| Table | Database | Rows | Coins | Date Range |
|-------|----------|------|-------|------------|
| FE_RESIDUAL_FEATURES | cp_backtest | 41,187 | 273 | 2025-07-19 to 2026-04-09 |

Migration: `migrations/017_fe_residual_features.sql`

## Pipeline Integration

- **Training**: 8 features added to `FEATURES_PRICE_ONLY` in `train_lgbm.py` (46 -> 54 features)
- **Dual-DB loader**: `FE_RESIDUAL_FEATURES` added to `fe_tables` dict (loaded from cp_backtest)
- **Materialized views**: Both `mv_ml_feature_matrix` (007) and `mv_ml_inference_matrix` (008) updated with LEFT JOIN
- **Backfill workflow**: `.github/workflows/backfill-residual-features.yml` (manual dispatch)

## New Files

```
src/features/residual_features.py                   — Compute engine (backfill + incremental)
migrations/017_fe_residual_features.sql              — DDL for FE_RESIDUAL_FEATURES
.github/workflows/backfill-residual-features.yml     — GitHub Actions backfill workflow
```

## Modified Files

```
src/models/train_lgbm.py                            — +8 features in FEATURES_PRICE_ONLY + fe_tables
migrations/007_mv_ml_feature_matrix.sql              — +8 columns + LEFT JOIN
migrations/008_mv_ml_inference_matrix.sql            — +8 columns + LEFT JOIN
```

## Retrain Results (price_only, 2026-04-11)

| Metric | Val (21d) | Test (14d) |
|--------|-----------|------------|
| IC-1d | 0.0643 | 0.0212 |
| IC-3d | -0.0073 | **0.2285** |
| IC-7d | 0.0400 | 0.1980 |
| Accuracy | 0.639 | 0.587 |
| Sharpe | 2.37 | -10.28 |
| Features | 42/42 | 42/42 |

Previous price_only had 34 features. WS6 adds 8 residual features (34 -> 42), all loaded with data from cp_backtest.

Top-3 SHAP features: `m_pct_1d` (0.0118), `d_pct_var` (0.0094), `d_pct_cum_ret` (0.0070)

Note: The residual features are new and may need more history + a retrain cycle to show full SHAP importance. The test IC-3d jump to 0.2285 is encouraging but should be validated across multiple windows.

## IC Improvement Plan Progress

| Workstream | Status | Description |
|------------|--------|-------------|
| WS6: Residual features | **Done** | 8 second-order features, backfilled, integrated |
| WS3: Cross-coin features | Pending | Momentum rank, sector rotation, market breadth |
| WS4: Regime detector | Pending | Fix 92% risk_on problem |
| WS2: News coverage | Pending | Expand beyond 63 coins |
| WS1: Top 250 universe | Pending | Filter micro-caps |
| WS5: Order book data | Pending | Funding rate, OI, long/short |

## Commits

```
7259779  feat: WS6 second-order residual features — 8 new alpha signals
```
