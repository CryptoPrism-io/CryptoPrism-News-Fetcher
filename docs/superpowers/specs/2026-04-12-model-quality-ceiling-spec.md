# Model Quality Ceiling — Full Spec

**Goal:** Push from 50 active features to 95+, complete all remaining workstreams, maximize IC before going live.

**Baseline (April 11, 2026):**
- Val IC-3d: 0.1286 | Test IC-3d: 0.1057 | Test Sharpe: 7.69
- Features: 50/50 in price_only LightGBM, 53/~111 in ensemble
- WS6 (residual) + WS3 (cross-coin) done. WS4/WS2/WS1 pending. WS5 deferred.

**Target:** 95+ active ensemble features, retrain-measured IC after each phase, per-phase HTML report.

---

## Execution Order

| Phase | Work | Features Added | Cumulative | Parallel Agents? |
|-------|------|----------------|------------|------------------|
| P1 | LSTM embedding backfill | +14 | 67 | No — sequential DB writes |
| P2 | TCN embedding backfill | +18 | 85 | No — sequential DB writes |
| P3 | News events + BTC context backfill | +13 | 98 | Yes — 2 agents (events + context) |
| P4 | WS4 regime detector rebuild | +0 (fixes gate) | 98 + gate | No — single module |
| P5 | WS2 news coverage expansion | +0 (widens existing 12) | 98 + wider | No — mapper + backfill |
| P6 | WS1 top 250 universe filter | +0 (cleaner data) | 98 filtered | No — config change |

**After EVERY phase:** retrain ensemble, measure IC/Sharpe, generate styled HTML report with before/after charts, convert to PDF, send to Telegram.

---

## Phase 1: LSTM Embedding Backfill

### What
Run trained `lstm_extractor.pt` inference on historical daily data to populate `ML_LSTM_EMBEDDINGS` table (currently 0 rows). This gives the ensemble 14 new features: 12 embedding dimensions + lstm_prob_buy + lstm_prob_sell.

### Why
The LSTM captures sequential price patterns (momentum persistence, reversal signals) that tree models miss. The 12-dim embedding compresses 30 days of temporal structure into a dense vector the ensemble can exploit.

### How
1. Trigger existing GitHub Actions workflow: `backfill-embeddings.yml` with input `model=lstm`
2. The workflow:
   - Restores `artifacts/lstm_extractor.pt` from cache
   - Runs `python -m src.inference.backfill_embeddings --model lstm`
   - Reads daily features per slug from cp_backtest (FE_BTC_RESIDUALS + FE_PCT_CHANGE + FE_NEWS_SIGNALS + FE_FEAR_GREED)
   - Builds 30-day sliding windows, forward-passes through LSTM on CPU
   - Upserts 12-dim embeddings + 2 probs to `ML_LSTM_EMBEDDINGS` on cp_backtest
3. Retrain ensemble: `gh workflow run "Daily ML Signals" -f mode=retrain_price`
4. Extract metrics from workflow logs
5. Generate `phase1-lstm-embeddings-report.html`

### Pre-checks
- Verify `artifacts/lstm_extractor.pt` exists in GitHub Actions cache (was saved during April 9 training)
- Verify `ML_LSTM_EMBEDDINGS` table exists on cp_backtest (migration 014)
- Verify `train_ensemble.py` already loads from this table (confirmed — lines 166-178)

### Expected impact
- Moderate IC lift — LSTM embeddings encode patterns orthogonal to tree features
- 14 new features, all with data after backfill

### Deliverables
- [ ] Trigger LSTM backfill workflow
- [ ] Verify ML_LSTM_EMBEDDINGS populated (row count, coin count, date range)
- [ ] Retrain ensemble
- [ ] Extract before/after IC, Sharpe, SHAP
- [ ] Generate phase1 HTML report with charts
- [ ] Convert to PDF, send to Telegram

---

## Phase 2: TCN Embedding Backfill

### What
Run trained `tcn_model.pt` inference on hourly data to populate `ML_TCN_EMBEDDINGS` table (currently 0 rows). 18 new features: 16 embedding dimensions + tcn_prob_buy + tcn_prob_sell.

### Why
The TCN captures hourly microstructure patterns — intraday momentum, reversal, and volume dynamics across 168-hour (7-day) windows. Its dilated convolutions see multi-day patterns that daily-only models miss.

### How
1. Trigger: `backfill-embeddings.yml` with input `model=tcn`
2. The workflow:
   - Restores `artifacts/tcn_model.pt` from cache
   - Runs `python -m src.inference.backfill_embeddings --model tcn`
   - Reads hourly OHLCV from `ohlcv_1h_250_coins` on cp_backtest_h
   - Builds 168h windows, forward-passes through TCN on CPU
   - Upserts to `ML_TCN_EMBEDDINGS` on cp_backtest
3. Retrain ensemble
4. Generate `phase2-tcn-embeddings-report.html`

### Pre-checks
- Verify `artifacts/tcn_model.pt` in cache
- Verify `ML_TCN_EMBEDDINGS` table exists (migration 013)
- This is the heaviest backfill — 250 coins x thousands of hourly windows. May need 120min timeout.

### Expected impact
- Moderate-to-high IC lift — hourly microstructure is a genuinely different signal source
- Combined with P1 LSTM: the ensemble now has both daily temporal + hourly microstructure views

### Deliverables
- [ ] Trigger TCN backfill workflow
- [ ] Verify ML_TCN_EMBEDDINGS populated
- [ ] Retrain ensemble
- [ ] Extract before/after metrics (cumulative: P1+P2 vs baseline)
- [ ] Generate phase2 HTML report — include LSTM vs TCN feature importance comparison
- [ ] PDF + Telegram

---

## Phase 3: News Events + BTC Context Backfill

### What
Two independent sub-tasks:

**3a. News Events:** Run `news_events.py --backfill` to classify all cc_news articles into event types and compute temporal features. Populates `FE_NEWS_EVENTS` (currently 0 rows on dbcp). Adds 10 features.

**3b. BTC Context:** Wire `btc_vol_7d` and `btc_momentum_24h` into the ensemble loader. These come from BTC's OHLCV data (already in cp_backtest), just need to be computed and joined. Adds 3 features.

### Why
- **Events:** Markets react differently to listings vs hacks vs regulatory news. `hours_since_hack` with negative sentiment is a strong short signal. `hours_since_listing` with volume spike is a long catalyst. The rule-based classifier already exists but the table is empty.
- **BTC Context:** Bitcoin's short-term momentum and volatility level condition how the model should interpret everything else. High BTC vol → residual signals are noisier → model should trust them less.

### How (sub-agent strategy)
These are independent — can run as **2 parallel agents**:

**Agent A (news events):**
1. Create GitHub Actions workflow `backfill-news-events.yml`
2. Run `python -m src.features.news_events --backfill` (reads cc_news from dbcp, writes FE_NEWS_EVENTS to dbcp)
3. Verify row counts

**Agent B (BTC context):**
1. Add BTC context computation to `train_ensemble.py` loader (compute btc_vol_7d and btc_momentum_24h from BTC rows in FE_PCT_CHANGE or 1K_coins_ohlcv)
2. Verify features load with data

**After both complete:**
3. Retrain ensemble
4. Generate `phase3-news-events-report.html`

### Pre-checks
- `FE_NEWS_EVENTS` table exists (migration 011)
- `cc_news` has 66K+ articles to classify
- `news_events.py` has `--backfill` flag ready
- `train_ensemble.py` already has `FEATURES_NEWS_EVENTS` and `FEATURES_BTC_CONTEXT` defined but some loader logic may need updating

### Expected impact
- News events: moderate lift (event-driven signals are sparse but high-conviction when present)
- BTC context: small lift (conditioning signals, not direct predictors)
- After P3: ensemble has ~98 active features

### Deliverables
- [ ] Create backfill-news-events.yml workflow
- [ ] Run news events backfill (Agent A)
- [ ] Wire BTC context features (Agent B)
- [ ] Retrain ensemble (cumulative P1+P2+P3)
- [ ] Generate phase3 report — event type distribution chart, coverage heatmap
- [ ] PDF + Telegram

---

## Phase 4: WS4 — Regime Detector Rebuild

### What
Replace the broken HMM regime detector (92% risk_on) with a rule-based system using volatility, breadth, and momentum thresholds.

### Why
The HMM's Gaussian assumption doesn't fit crypto volatility distributions. 92% risk_on means the regime gate is doing nothing — every trade is treated identically regardless of market conditions. A transparent rule-based system lets us tune thresholds directly and debug easily.

### How

**New regime logic (rule-based):**
```
risk_on:   breadth > 0.55 AND btc_mom_72h > 0 AND btc_vol_ratio < 1.5
risk_off:  breadth < 0.35 OR (btc_mom_72h < -0.05 AND fear_greed < 30)
breakout:  btc_vol_ratio > 2.0 AND abs(btc_mom_24h) > 0.03
choppy:    everything else
```

**Steps:**
1. Add `rule_based_regime()` function to `src/models/regime.py`
2. Backfill `ML_REGIME` with new classifications
3. Update `train_ensemble.py` `apply_regime_gating()` to use new states
4. Retrain ensemble with improved gating
5. Generate `phase4-regime-report.html`

### Pre-checks
- `ML_REGIME` table exists (migration 012)
- `regime.py` has existing HMM code — keep it, add rule-based alternative
- Breadth data available from `FE_CROSS_COIN.cc_breadth_20d` (WS3, already populated)

### Expected impact
- Risk management improvement rather than raw IC lift
- Better regime distribution: expect ~40-50% risk_on, ~20% choppy, ~15% risk_off, ~15% breakout
- Regime-conditioned trading: model learns to be cautious in risk_off, aggressive in breakout

### Deliverables
- [ ] Implement `rule_based_regime()` in regime.py
- [ ] Backfill ML_REGIME with new classifications
- [ ] Update ensemble gating
- [ ] Retrain ensemble (cumulative P1-P4)
- [ ] Generate phase4 report — regime distribution before/after, regime-conditioned IC breakdown
- [ ] PDF + Telegram

---

## Phase 5: WS2 — News Coverage Expansion

### What
Expand `coin_mapper.py` from 53 mapped coins to 150+ using fuzzy name matching (coin name in article title/body). Rerun sentiment pipeline for newly matched coins.

### Why
Currently only 53 coins have news features. The other ~200 tradeable coins get NaN for all 12 news columns. Expanding coverage means the model can use news signals for 3x more coins.

### How
1. **Expand coin_mapper.py:**
   - Load all coin slugs from `1K_coins_ohlcv` (or top 250 by market cap)
   - For each slug, build name variants (e.g., "solana" → ["Solana", "SOL", "$SOL"])
   - Match against cc_news article titles and bodies using case-insensitive substring matching
   - Quality filter: require >=3 articles per coin to include (avoid false positives)

2. **Rerun sentiment backfill:**
   - GitHub Actions: `backfill-news.yml` with expanded mapper
   - Regenerate `FE_NEWS_SENTIMENT` for newly matched coins
   - Regenerate `FE_NEWS_SIGNALS` aggregations

3. **Retrain:**
   - More coins now have non-NaN news features
   - LightGBM handles NaN natively, so this selectively improves coins that gain coverage

### Pre-checks
- `coin_mapper.py` currently has 64 hardcoded token→slug mappings
- `cc_news` articles have `categories` and full body text for matching
- FinBERT sentiment pipeline exists and runs on GitHub Actions (CPU mode for backfill)

### Expected impact
- Moderate IC lift for the expanded universe — news signals now inform predictions for 150+ coins instead of 53
- No impact on coins that already had coverage

### Deliverables
- [ ] Expand coin_mapper.py with fuzzy matching
- [ ] Run expanded sentiment backfill
- [ ] Regenerate FE_NEWS_SIGNALS
- [ ] Retrain ensemble (cumulative P1-P5)
- [ ] Generate phase5 report — coverage map (which coins gained news), sentiment distribution
- [ ] PDF + Telegram

---

## Phase 6: WS1 — Top 250 Universe Filter

### What
Filter training data to coins with market_cap > $50M (roughly the top 250). Drop ~700+ micro-caps with sparse, noisy data.

### Why
Micro-caps have irregular trading, thin liquidity, and unreliable OHLCV data. Training on them introduces noise that dilutes the signal for tradeable coins. Filtering improves data quality without losing any coins we'd actually trade.

### How
1. Add market cap filter in `train_lgbm.py` and `train_ensemble.py`:
   ```python
   # After loading labels, filter by market cap
   df = df[df['slug'].isin(top_250_slugs)]
   ```
2. Compute `top_250_slugs` from `1K_coins_ohlcv` — average market_cap over last 30 days > $50M
3. Apply same filter to inference (only generate signals for tradeable coins)
4. Retrain on cleaner subset

### Pre-checks
- `1K_coins_ohlcv` has market_cap column
- Need to verify market_cap data quality (not all coins may have it)

### Expected impact
- IC lift from cleaner training data — less noise in the learning process
- Sharper signals for the coins we actually care about
- Smaller model artifacts (fewer rows to train on)

### Deliverables
- [ ] Implement universe filter
- [ ] Retrain ensemble (cumulative P1-P6, final model)
- [ ] Generate phase6 report — FINAL comprehensive report with IC waterfall across all 6 phases
- [ ] PDF + Telegram

---

## Sub-Agent Development Strategy

### When to use parallel agents
- **P3 only:** News events backfill (Agent A) and BTC context wiring (Agent B) are independent
- All other phases are sequential (each depends on the previous retrain)

### Per-phase agent pattern
Each phase follows the same template:
1. **Build/modify code** (if needed — P1/P2 just trigger existing workflows)
2. **Trigger GitHub Actions** workflow
3. **Monitor** workflow completion
4. **Extract metrics** from logs (IC, Sharpe, feature counts, SHAP)
5. **Generate HTML report** with D3.js charts (matching system-architecture.html design)
6. **Convert to PDF** via Playwright
7. **Send to Telegram**
8. **Commit** report + any code changes

### Report template (reused across all phases)
Each phase report includes:
- Cover with phase number and title
- Before/after metrics table (IC, Sharpe, MaxDD, accuracy, features)
- Bar chart: feature count progression
- SHAP importance chart (highlight new features)
- Specific charts per phase (embedding distributions, regime breakdown, coverage map, etc.)
- Cumulative IC waterfall across all completed phases
- Footer: CryptoPrism-io | Yogesh Sahu | Page X of Y

---

## Dependencies & Packages

**Already installed in workflows — no new packages needed:**

| Package | Used by | Workflow |
|---------|---------|----------|
| `torch` (CPU) | LSTM/TCN inference | backfill-embeddings.yml |
| `lightgbm` | Ensemble training | daily-ml-signals.yml |
| `scikit-learn` | Classification metrics | daily-ml-signals.yml |
| `shap` | Feature importance | daily-ml-signals.yml |
| `hmmlearn` | Regime HMM (kept for comparison) | daily-ml-signals.yml |
| `scipy` | Spearman IC | daily-ml-signals.yml |
| `psycopg2-binary` | All DB access | requirements.txt |
| `pandas`, `numpy` | Everything | requirements.txt |
| `playwright` | PDF generation | Local only |

**For P5 (fuzzy matching):** May need `rapidfuzz` or `thefuzz` — add to requirements.txt when we get there.

---

## Success Criteria

| Metric | Current | Target | Stretch |
|--------|---------|--------|---------|
| Active ensemble features | 53 | 95+ | 98 |
| Val IC-3d | 0.1286 | 0.15+ | 0.20+ |
| Test Sharpe | 7.69 | Positive (stable) | 10+ |
| Regime distribution | 92% risk_on | <60% risk_on | ~40% |
| News coverage | 53 coins | 150+ coins | 200+ |
| Training universe | ~1000 coins | 250 focused | 250 |

---

## Risk Factors

1. **LSTM/TCN artifacts may not be in GitHub Actions cache** — if expired, need to retrain models first (GPU required, run locally)
2. **TCN backfill may timeout** (168h windows x 250 coins x years of data) — may need chunked backfill
3. **News events backfill depends on cc_news article body quality** — some articles may be truncated
4. **Universe filter may reduce training set size below viable minimum** — monitor val/test set sizes
5. **Feature collinearity** — adding 45 features risks overfitting if many are redundant. Monitor SHAP and consider feature selection if IC drops.
