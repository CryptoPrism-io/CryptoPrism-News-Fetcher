# Alpha Ensemble Architecture — Design Spec

**Date**: 2026-04-09
**Goal**: Build a multi-model ensemble that finds alpha beyond BTC-correlated moves, with hourly inference cadence, supporting both market timing (B) and event-driven trades (C).

**Compute**: RTX 3070 8GB for training. Inference on GitHub Actions CPU or GCP VM. ONNX export for all neural models.

---

## Problem Statement

Crypto returns are ~80% correlated to BTC. The current LightGBM model sees single-point-in-time snapshot features — it cannot distinguish "ETH dropping because BTC is dropping" from "ETH dropping because of a smart contract exploit." Top-25 coin signals have negative spread because large caps move in lockstep.

We need models that capture temporal patterns, regime shifts, and information asymmetry to find the 20% that ISN'T BTC.

---

## Architecture Overview

```
                    cc_news articles          economic_calendar_ff
                          |                          |
                   [News Event Detector]     [Macro Regime Model]
                          |                          |
                    FE_NEWS_EVENTS              ML_REGIME
                          |                          |
hourly OHLCV -----> [BTC Residual Decomposition] <---+
    |                     |
    |              FE_BTC_RESIDUALS
    |                /          \
    v               v            v
[TCN 7d hourly]  [LSTM 30d daily]  [Enhanced LightGBM]
    |               |                     |
ML_TCN_EMBEDDINGS  ML_LSTM_EMBEDDINGS     |
    \               |                    /
     \              |                   /
      +-------------+------------------+
                    |
           [Ensemble Meta-Learner]
                    |
              ML_SIGNALS_V2
```

Six components, executed in order:

1. BTC Residual Decomposition (foundation layer)
2. News Event Detector (NLP pipeline)
3. Macro Regime Model (market-wide state)
4. Temporal Conv Net — TCN (hourly patterns)
5. LSTM Feature Extractor (daily patterns)
6. Enhanced LightGBM + Ensemble Meta-Learner (final signal)

---

## Component 1: BTC Residual Decomposition

**Purpose**: Strip BTC beta from every coin's returns so downstream models see only idiosyncratic alpha.

**Method**: Rolling 30-day OLS regression per coin:
```
coin_ret_1h = alpha + beta * btc_ret_1h + epsilon
```

**Outputs per coin per timestamp**:
- `beta_30d`: BTC sensitivity coefficient
- `alpha_30d`: intercept (persistent over/underperformance)
- `residual_1h`: epsilon for hourly data
- `residual_1d`: daily aggregated residual
- `residual_vol_ratio`: residual vol / total vol (how much is idiosyncratic)

**Table**: `FE_BTC_RESIDUALS` on cp_backtest
- Schema: `(slug, timestamp, beta_30d, alpha_30d, residual_1h, residual_1d, residual_vol_ratio)`
- Populated: hourly, from `ohlcv_1h_250_coins` + `1K_coins_ohlcv`
- Backfill: full 14 months of hourly data

**Dependencies**: None (foundation layer).

---

## Component 2: News Event Detector

**Purpose**: Classify news articles by event type and estimate price impact magnitude. Converts raw text into structured, actionable features.

**Pipeline**:
1. Fine-tune DistilBERT/FinBERT on cc_news articles into 7 event categories:
   - `listing`, `hack_exploit`, `regulatory`, `partnership`, `tokenomics`, `macro`, `neutral`
2. Magnitude lookup table: median 1d/3d return after each historical event type.
3. Generate features per coin per hour.

**Training data**: ~500 manually labeled articles (or weak-labeled via Claude API), fine-tune on full 83K corpus.

**Output features per coin per hour**:
- `hours_since_event_[listing|hack|regulatory|partnership|tokenomics|macro]` (6 features)
- `event_magnitude_estimate`: expected % move from lookup table
- `event_count_24h`: unusual activity flag
- `news_surprise`: current sentiment minus 7d rolling mean
- `cross_coin_news_ratio`: this coin's news volume vs market average

**Table**: `FE_NEWS_EVENTS` on dbcp
- Schema: `(slug, timestamp, event_type, magnitude_est, hours_since_listing, hours_since_hack, hours_since_regulatory, hours_since_partnership, hours_since_tokenomics, hours_since_macro, event_count_24h, news_surprise, cross_coin_news_ratio)`

**Dependencies**: cc_news table, existing FinBERT infrastructure.

---

## Component 3: Macro Regime Model

**Purpose**: Classify market-wide regime to gate signal confidence. Prevents "shorting in a bull market" failures like Apr 6.

**Inputs** (all market-wide, no per-coin):
- `fear_greed_index` (from FE_FEAR_GREED_CMC)
- BTC realized volatility 7d, 30d (from 1K_coins_ohlcv)
- BTC volume profile: current vs 30d average
- BTC hourly momentum: 24h and 72h trend (from ohlcv_1h_250_coins)
- Economic calendar: days until next FOMC/CPI/NFP, impact level (from economic_calendar_ff)
- Market breadth: % of top 50 coins above 20d MA (from 1K_coins_ohlcv)
- Risk appetite proxy: top quartile minus bottom quartile return spread

**Model**: Hidden Markov Model (HMM), 4 states:
- **Risk-On**: broad rally, high breadth, low vol, positive momentum
- **Risk-Off**: broad selloff, declining breadth, rising vol
- **Choppy**: no trend, mixed signals, high vol
- **Breakout**: sudden regime change, vol spike + directional move

**Outputs**:
- `regime_state`: categorical (risk_on, risk_off, choppy, breakout)
- `regime_confidence`: 0-1
- `transition_prob_*`: probability of switching to each state in next 24h

**Table**: `ML_REGIME` on dbcp
- Schema: `(timestamp, regime_state, confidence, trans_prob_risk_on, trans_prob_risk_off, trans_prob_choppy, trans_prob_breakout)`
- One row per hour (market-wide, not per-coin)

**Why HMM**: 3-4 states, sequential transitions, small dataset — HMMs are purpose-built for this. Interpretable, fast, no overfitting risk.

**Dependencies**: FE_FEAR_GREED_CMC, 1K_coins_ohlcv, ohlcv_1h_250_coins, economic_calendar_ff.

---

## Component 4: Temporal Conv Net (TCN)

**Purpose**: Detect intraday microstructure patterns (breakouts, consolidations, volume spikes) from hourly residual sequences.

**Input**: Per coin, matrix of shape `(168, 8)` — 7-day hourly lookback:
- residual_1h, residual_volume (from BTC decomposition)
- price spread: (high-low)/close
- close-open direction
- hourly volume z-score vs own 7d rolling mean
- hour-of-day encoding (sin, cos) — captures session patterns
- rolling 24h volatility of residuals

**Architecture**: 1D Causal CNN with dilated convolutions:
- 4 residual blocks, dilation [1, 2, 4, 8] — receptive field = full 168h
- Each block: dilated conv (64 filters) -> batch norm -> ReLU -> dropout(0.3)
- Two output heads:
  - Classification: softmax -> BUY/HOLD/SELL (3d residual direction)
  - Embedding: linear -> 16-dim vector

**Training**: Walk-forward on hourly residuals from cp_backtest_h. GPU locally, export ONNX.

**Table**: `ML_TCN_EMBEDDINGS` on cp_backtest
- Schema: `(slug, timestamp, emb_0..emb_15, tcn_prob_buy, tcn_prob_sell, tcn_direction)`

**Dependencies**: FE_BTC_RESIDUALS (Component 1).

---

## Component 5: LSTM Feature Extractor

**Purpose**: Capture multi-week temporal narratives (accumulation phases, slow bleeds, capitulation patterns) that the hourly TCN window is too short to see.

**Input**: Per coin, sequence of shape `(30, 12)` — 30-day daily lookback:
- residual_1d, residual_volume_1d (from BTC decomposition)
- raw close-to-close return, daily range (high-low)/close
- volume z-score vs 30d mean
- rolling 7d and 14d residual volatility
- momentum rank among all coins (percentile 0-1)
- news_sentiment_1d, news_volume_1d
- fear_greed_index
- market_breadth

**Architecture**:
- 2-layer LSTM, hidden_size=64, dropout=0.3
- Final hidden state only (summary, not sequence-to-sequence)
- Two heads:
  - Embedding: linear -> 12-dim vector
  - Auxiliary classification: softmax -> BUY/HOLD/SELL (training signal only)

**Training**: Walk-forward on daily data from cp_backtest. GPU locally, export ONNX.

**Table**: `ML_LSTM_EMBEDDINGS` on cp_backtest
- Schema: `(slug, timestamp, lemb_0..lemb_11, lstm_prob_buy, lstm_prob_sell)`

**Complementarity with TCN**:
- TCN (hourly, 7d): "breakout pattern forming in last 48 hours"
- LSTM (daily, 30d): "3-week accumulation phase building"

**Dependencies**: FE_BTC_RESIDUALS (Component 1), FE_NEWS_SIGNALS, FE_FEAR_GREED_CMC.

---

## Component 6: Enhanced LightGBM + Ensemble Meta-Learner

**Purpose**: Combine all signals into a final prediction with confidence-adjusted scoring.

### 6a. Enhanced LightGBM

**Feature set** (~99 features):

| Block | Count | Source |
|-------|-------|--------|
| Original price features | 46 | Current model (pct, mom, osc, tvv, rat, fg, news) |
| BTC Residual | 5 | beta_30d, alpha_30d, residual_1d, residual_vol_ratio, beta_change_7d |
| TCN Embeddings | 18 | emb_0..15, tcn_prob_buy, tcn_prob_sell |
| LSTM Embeddings | 14 | lemb_0..11, lstm_prob_buy, lstm_prob_sell |
| News Events | 10 | hours_since_*, magnitude, surprise, cross_coin_ratio |
| BTC-Relative | 6 | residual returns 1d/3d, btc_vol 7d/30d, btc_momentum_24h, market_breadth |

**Training target**: `label_3d_residual` — 3-day residual return bucket (after removing BTC beta), NOT raw returns.

### 6b. Ensemble Meta-Learner

Stacking model that combines individual model outputs:
- LightGBM probabilities (3)
- TCN probabilities (2)
- LSTM probabilities (2)
- Regime state one-hot (4) + confidence (1)
- News event flags (active events binary)

**Model**: Logistic regression or small gradient boost (deliberately simple to avoid overfitting the ensemble layer).

**Regime gating logic**:
- Risk-On: trust BUY signals, discount SELL by `1 - regime_confidence`
- Risk-Off: trust SELL signals, discount BUY by `1 - regime_confidence`
- Choppy: reduce all confidence by 50%
- Breakout: increase confidence for signals aligned with breakout direction

**Output table**: `ML_SIGNALS_V2` (replaces current ML_SIGNALS)
- Same schema as ML_SIGNALS plus: `regime_state`, `tcn_direction`, `lstm_direction`, `ensemble_confidence`, `residual_score`

**Dependencies**: All previous components.

---

## New Database Tables Summary

| Table | Database | Frequency | Rows/day (est.) |
|-------|----------|-----------|-----------------|
| FE_BTC_RESIDUALS | cp_backtest | hourly | ~6,000 (250 coins x 24h) |
| FE_NEWS_EVENTS | dbcp | hourly | ~250 (per coin per scan) |
| ML_REGIME | dbcp | hourly | 24 (market-wide) |
| ML_TCN_EMBEDDINGS | cp_backtest | hourly | ~6,000 |
| ML_LSTM_EMBEDDINGS | cp_backtest | daily | ~1,000 |
| ML_SIGNALS_V2 | dbcp | hourly | ~6,000 |

---

## Inference Pipeline (Hourly Cron)

```
Every 1-4 hours:
  1. Compute BTC residuals for latest hour          (~5 sec)
  2. Scan cc_news for new articles -> FE_NEWS_EVENTS (~10 sec)
  3. Update ML_REGIME with latest market state       (~2 sec)
  4. Run TCN on latest 168h window per coin          (~30 sec on CPU/ONNX)
  5. Run LSTM on latest 30d window per coin          (~15 sec on CPU/ONNX)
  6. Run Enhanced LightGBM with all features          (~5 sec)
  7. Run Ensemble Meta-Learner -> ML_SIGNALS_V2       (~2 sec)
  Total: ~70 sec on CPU (fits GitHub Actions easily)
```

---

## Training Schedule

| Model | Frequency | Compute | Duration |
|-------|-----------|---------|----------|
| BTC Residual | No training (OLS computed at inference) | CPU | — |
| News Event Detector | Monthly or on new labeled data | GPU | ~20 min |
| Macro Regime HMM | Weekly (Sunday retrain) | CPU | ~1 min |
| TCN | Weekly (Sunday retrain) | GPU | ~30 min |
| LSTM | Weekly (Sunday retrain) | GPU | ~15 min |
| Enhanced LightGBM | Weekly (Sunday retrain) | CPU | ~2 min |
| Ensemble Meta-Learner | Weekly (Sunday retrain) | CPU | ~1 min |

---

## Implementation Order

1. **BTC Residual Decomposition** — foundation, unblocks everything
2. **LSTM Feature Extractor** — uses daily data we already have in cp_backtest
3. **TCN** — needs hourly residuals (depends on step 1 + cp_backtest_h)
4. **News Event Detector** — independent, can parallel with 2-3
5. **Macro Regime Model** — independent, can parallel with 2-3
6. **Enhanced LightGBM** — needs 1-5 complete
7. **Ensemble Meta-Learner** — needs all above
8. **Hourly inference pipeline** — wire everything into cron

---

## Success Criteria

- Top-25 coin spread: **positive** on 1d and 3d horizons (currently negative)
- Overall IC-3d: **> 0.10** (currently 0.047 on 30d backtest)
- ICIR: **> 0.3** (currently 0.168)
- Regime model correctly identifies at least 70% of major reversals
- Event detector catches listing/hack events within 1 hour
- Hourly inference completes in < 2 minutes on CPU
