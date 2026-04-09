# Alpha Ensemble Build — April 9, 2026

## Summary

Built and deployed the full 6-component ensemble ML architecture for crypto signal generation. All models trained, pipelines wired, GitHub Actions workflow ready.

## Components Delivered

| Component | Status | Key Metric |
|-----------|--------|------------|
| BTC Residual Decomposition | Deployed | 993K rows, 288 coins |
| Macro Regime HMM | Trained + Backfilled | 4,699 daily states (risk_on/risk_off/choppy/breakout) |
| LSTM Feature Extractor | Trained on GPU | val_acc=73.2%, 25K sequences, 12-dim embeddings |
| TCN Hourly Microstructure | Trained on GPU | val_acc=70.0%, 684K sequences, 16-dim embeddings |
| News Event Detector | Rule-based deployed | 7 event types (listing, hack, regulatory, etc.) |
| Enhanced LightGBM Ensemble | Trained | 53/95 features active, IC-3d=0.086, IC-7d=0.213 |
| Hourly Inference Pipeline | Working end-to-end | ML_SIGNALS_V2 table, 985 signals per run |
| GitHub Actions Workflow | Ready | `hourly-ensemble.yml` — every 4 hours |

## New Database Tables

| Table | Database | Rows |
|-------|----------|------|
| FE_BTC_RESIDUALS | cp_backtest | 993,619 |
| FE_NEWS_EVENTS | dbcp | 0 (needs backfill) |
| ML_REGIME | dbcp | 4,699 |
| ML_TCN_EMBEDDINGS | cp_backtest | 0 (needs inference backfill) |
| ML_LSTM_EMBEDDINGS | cp_backtest | 0 (needs inference backfill) |
| ML_SIGNALS_V2 | dbcp | 985 |

## New Files

```
src/features/btc_residuals.py       — BTC beta decomposition (rolling 30d OLS)
src/features/news_events.py         — News event classifier + temporal features
src/models/regime.py                — 4-state HMM (risk_on/off/choppy/breakout)
src/models/lstm_extractor.py        — 2-layer LSTM (30d daily, 12-dim embedding)
src/models/tcn.py                   — 1D causal TCN (168h hourly, 16-dim embedding)
src/models/train_ensemble.py        — Enhanced LightGBM (~95 features) + meta-learner
src/inference/hourly_signals.py     — Hourly ensemble inference -> ML_SIGNALS_V2
migrations/010-015                  — 6 new table/index migrations
tests/test_*.py                     — 21 tests (all passing)
.github/workflows/hourly-ensemble.yml        — Every 4h inference cron
.github/workflows/backfill-btc-residuals.yml — One-shot backfill workflow
```

## Model Artifacts

| Artifact | Size | Trained On |
|----------|------|------------|
| lstm_extractor.pt | PyTorch | CUDA (RTX 3070), 30 epochs |
| tcn_model.pt | PyTorch | CUDA (RTX 3070), 30 epochs |
| regime_hmm.pkl | hmmlearn | CPU, 200 iterations |
| lgbm_ensemble_v1.pkl | LightGBM | CPU, 500 trees |

## Known Limitations (Cold Start)

The ensemble currently operates with 53/95 features — the remaining 42 features (LSTM/TCN embeddings, news events, BTC context) need their inference pipelines to populate the DB tables. Expected timeline:

- **Immediate**: Run LSTM + TCN inference on historical data to populate embedding tables
- **1 week**: News events accumulate from daily article classification
- **After backfill**: Retrain ensemble with all 95 features for full signal quality

## Performance Context

| Model | IC-3d | Sharpe | Features |
|-------|-------|--------|----------|
| Old LightGBM (blind) | -0.007 | -2.16 | 1/54 (fear_greed only) |
| Fixed LightGBM (dual-DB) | +0.081 | +6.18 | 46/46 |
| New Ensemble (partial) | +0.086 | — | 53/95 |
| Target (full features) | >0.10 | >0.30 ICIR | 95/95 |

## Commits

```
5c58ac5  feat: BTC residual decomposition (foundation layer)
fe5b716  feat: macro regime HMM + news event detector
d02b858  feat: LSTM + TCN neural models
064c84b  perf: COPY + temp table for fast bulk upsert
79bf026  fix: LSTM training fixes + ONNX skip
d817300  feat: TCN trained (val_acc=70%)
48bc710  feat: ensemble LightGBM (IC-3d=0.086)
4f6b2bf  fix: hourly inference without labels
```
