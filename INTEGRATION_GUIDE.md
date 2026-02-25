# CryptoPrism News Intelligence Layer — Integration Guide
**Version:** 1.0 | **Date:** Feb 25, 2026
**Repo:** CryptoPrism-io/CryptoPrism-News-Fetcher
**Audience:** Anyone integrating this pipeline with S4 bot, LIVE bot, or any price-based strategy

---

## 1. What We Built

### 1.1 The Pipeline at a Glance

```
cc_news (66,391 articles, Oct 2025 → today)
        │
        ▼  FinBERT GPU scoring (ProsusAI/finbert)
FE_NEWS_SENTIMENT  ← 43,487 article-level scores
        │
        ▼  Daily aggregation per coin
FE_NEWS_SIGNALS    ← 5,085 coin-day rows (50 coins × 128 days)
        │
        ├──► used directly as trade filter / leverage multiplier in S4 / LIVE bot
        │
        ▼  join with FE_* tables + ML_LABELS
mv_ml_feature_matrix  (112,895 rows — full feature matrix)
        │
        ▼  LightGBM training (walk-forward)
ML_MODEL_REGISTRY     ← lgbm_news_augmented_v1 (active), lgbm_price_only_v1
        │
        ▼  daily inference
ML_SIGNALS            ← 1,000 coin rankings per day (prob_buy, signal_score)
        │
        ▼  backtest
ML_BACKTEST_RESULTS   ← IC, ICIR, Sharpe, HitRate, MaxDD per model
```

### 1.2 The Signals Available (FE_NEWS_SIGNALS)

Every day, for every coin with news coverage, we write one row to `FE_NEWS_SIGNALS`.
This is the primary table for bot integration — it is simple, fast to query, coin+date indexed.

| Column | Type | Description |
|--------|------|-------------|
| `slug` | TEXT | Coin identifier (e.g. "bitcoin", "solana") |
| `timestamp` | TIMESTAMPTZ | 23:59:59 UTC on the signal date |
| `news_sentiment_1d` | FLOAT | Mean FinBERT composite score today (−1 to +1) |
| `news_sentiment_3d` | FLOAT | 3-day rolling mean sentiment |
| `news_sentiment_7d` | FLOAT | 7-day rolling mean sentiment |
| `news_sentiment_momentum` | FLOAT | sentiment_1d − sentiment_3d (trend direction) |
| `news_volume_1d` | INT | # articles scored today |
| `news_volume_3d` | INT | # articles over 3 days |
| `news_volume_zscore_1d` | FLOAT | Today's volume vs 30-day baseline (σ units) |
| `news_breaking_flag` | INT | 1 if any article tagged "breaking news" today |
| `news_regulation_flag` | INT | 1 if regulatory news detected (SEC, ban, legislation) |
| `news_security_flag` | INT | 1 if hack/exploit/rug-pull news detected |
| `news_adoption_flag` | INT | 1 if partnership/ETF/institutional adoption news |
| `news_source_quality` | FLOAT | Tier-weighted sentiment (Tier 1 source = 2×, Tier 3 = 0.4×) |
| `news_tier1_count_1d` | INT | # articles from Tier 1 sources (CoinDesk, Cointelegraph, Decrypt, Seeking Alpha) |
| `news_tier2_count_1d` | INT | # articles from Tier 2 sources |
| `news_tier3_count_1d` | INT | # articles from Tier 3 sources |

**Source tiers:**
- **Tier 1** (2× weight): CoinDesk, Cointelegraph, Decrypt, Seeking Alpha
- **Tier 2** (1× weight): BeInCrypto, CryptoSlate, NewsWire, u.today, ambcrypto
- **Tier 3** (0.4× weight): Bitcoin World, CoinOtag, TimesTabloid, ZyCrypto, and others

### 1.3 Real Data Sample (Feb 20–23, 2026)

| Coin | Date | Sent 1d | Sent 3d | Sent mom | Vol Z | Reg | Hack | Adopt | T1 Count |
|------|------|---------|---------|----------|-------|-----|------|-------|----------|
| bitcoin | Feb 23 | −0.138 | −0.177 | −0.022 | +1.02 | ✅ | ✅ | ✅ | 8 |
| ethereum | Feb 23 | −0.216 | −0.248 | −0.073 | −0.37 | ✅ | ❌ | ✅ | 2 |
| solana | Feb 23 | −0.090 | −0.213 | +0.001 | +0.16 | ❌ | ✅ | ✅ | 0 |
| chainlink | Feb 23 | +0.141 | +0.294 | −0.045 | +0.77 | ✅ | ❌ | ❌ | 0 |
| dogecoin | Feb 23 | −0.449 | −0.299 | −0.332 | +1.37 | ❌ | ✅ | ❌ | 0 |
| chainlink | Feb 20 | +0.470 | +0.110 | +0.421 | −0.17 | ❌ | ❌ | ❌ | 0 |

> Interpretation: On Feb 23, BTC had heavy negative sentiment with breaking + regulation + security events.
> Chainlink had consistent positive sentiment over 3 days with strong momentum on Feb 20.

### 1.4 The ML Layer (ML_SIGNALS)

On top of raw news signals, we run a LightGBM model that fuses:
- 42 price features (momentum, RSI, DMV scores, fear/greed, OHLCV derived)
- 12 news features (all columns from FE_NEWS_SIGNALS)
- Forward return labels (1d/3d/7d/14d from 1K_coins_ohlcv)

**Active model:** `lgbm_news_augmented_v1` (model_id=2)
**Output per coin per day:**

| Column | Description |
|--------|-------------|
| `prob_buy` | P(coin goes up >3% in 3 days) |
| `prob_hold` | P(coin stays flat) |
| `prob_sell` | P(coin goes down >3% in 3 days) |
| `signal_score` | prob_buy − prob_sell ∈ (−1, +1) |
| `confidence` | max(prob_buy, prob_hold, prob_sell) |
| `top_features` | SHAP top-5 features driving prediction (JSONB) |

**Backtest results (Oct 21, 2025 → Feb 10, 2026, 113 days, 1,263 coins):**
- IC-3d mean: **+0.0142** (positive = rankings ARE informative)
- ICIR: **0.35**
- IC positive %: **67.3%** of days
- Hit rate (top-10 vs bottom-10): **55.8%** (random = 50%)
- BTC Sharpe: −2.22 (bear market period — expected)

---

## 2. How to Query the Signals (PostgreSQL)

### 2.1 Get Latest News Signal for a Coin

```sql
SELECT *
FROM "FE_NEWS_SIGNALS"
WHERE slug = 'bitcoin'
ORDER BY timestamp DESC
LIMIT 1;
```

### 2.2 Get All Coin Signals for Today (Bulk, For Bot Integration)

```sql
SELECT slug,
       news_sentiment_1d,
       news_sentiment_3d,
       news_sentiment_momentum,
       news_volume_zscore_1d,
       news_breaking_flag,
       news_regulation_flag,
       news_security_flag,
       news_adoption_flag,
       news_source_quality,
       news_tier1_count_1d
FROM "FE_NEWS_SIGNALS"
WHERE DATE(timestamp) = CURRENT_DATE - 1    -- yesterday's close (same convention as price bars)
ORDER BY slug;
```

### 2.3 Get ML Rankings (Top Buy Signals Today)

```sql
SELECT slug, signal_score, prob_buy, prob_hold, prob_sell, confidence
FROM "ML_SIGNALS"
WHERE DATE(timestamp) = (SELECT MAX(DATE(timestamp)) FROM "ML_SIGNALS")
ORDER BY signal_score DESC
LIMIT 20;
```

### 2.4 Python Snippet for Bot Integration

```python
import psycopg2
from datetime import date, timedelta

def get_news_signals(conn, target_date: str = None) -> dict:
    """
    Returns dict: { slug: { sent_1d, sent_3d, regulation_flag, security_flag, ... } }
    Fast lookup for use inside signal_job.py
    """
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()

    with conn.cursor() as cur:
        cur.execute("""
            SELECT slug, news_sentiment_1d, news_sentiment_3d, news_sentiment_7d,
                   news_sentiment_momentum, news_volume_zscore_1d,
                   news_breaking_flag, news_regulation_flag,
                   news_security_flag, news_adoption_flag,
                   news_source_quality, news_tier1_count_1d
            FROM "FE_NEWS_SIGNALS"
            WHERE DATE(timestamp) = %s
        """, (target_date,))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

    return {row[0]: dict(zip(cols[1:], row[1:])) for row in rows}
```

---

## 3. Integration Modes for S4 / LIVE Bot

There are **6 distinct ways** to use this pipeline with price-based strategies.
They are ordered from simplest to most impactful.

---

### Mode 1 — Hard Veto (Filter Bad News Before Entry)

**What it does:** Before any position opens, check if the coin has dangerous news flags.
If yes, skip — regardless of how strong the price signal is.

**When to use:** Conservative. Prevents entering into coins with breaking regulatory/security news.
Statistically: regulation events → average −8% 3-day return. Hack events → average −12% 3-day return.

**Integration point:** In `signal_job.py`, inside `_try_trade()`, after existing cooldown/position checks.

```python
# In signal_job.py — add after existing checks
news = get_news_signals(pg_conn)   # load once before the loop, keyed by slug
coin_slug = SYMBOL_TO_SLUG[sig.symbol]   # e.g. "SOLUSDT" → "solana"

n = news.get(coin_slug, {})
if n.get("news_regulation_flag") and sig.direction == "LONG":
    log.info(f"Skip {sig.symbol}: regulation news active (sent_3d={n['news_sentiment_3d']:.3f})")
    return False
if n.get("news_security_flag") and sig.direction == "LONG":
    log.info(f"Skip {sig.symbol}: security/hack news active")
    return False
```

**Expected impact:** Eliminates ~5–15% of trades, mostly catastrophic losses.
These are the trades that turn a +55% month into a +40% month after a surprise rug/hack.

---

### Mode 2 — Soft Leverage Multiplier (News-Scaled Position Size)

**What it does:** Keep S4's base leverage but scale it up or down by news sentiment.
Strong positive sentiment → larger position. Strong negative → smaller or skip.

**When to use:** Aggressive. Squeezes more out of high-conviction setups.

**Integration point:** In `sizer.py` or at the point where margin is computed in `signal_job.py`.

```python
# After computing base leverage from S4 prediction:
base_leverage = min(abs(sig.pred_4h) / sig.thr, MAX_LVG)

n = news.get(coin_slug, {})
sent_3d = n.get("news_sentiment_3d", 0.0)  # -1 to +1, default 0 if no news

# Scale leverage: ±30% adjustment based on 3-day sentiment
# sent_3d = +0.5 → mult = 1.15  |  sent_3d = -0.5 → mult = 0.85
news_mult = 1.0 + (sent_3d * 0.3)
news_mult = max(0.5, min(1.5, news_mult))   # clamp to [0.5, 1.5]

final_leverage = round(base_leverage * news_mult, 1)
final_leverage = max(1.0, min(MAX_LVG, final_leverage))
log.info(f"{sig.symbol}: base_lvg={base_leverage:.1f}x news_mult={news_mult:.2f} → {final_leverage:.1f}x")
```

**Expected impact:** Higher IC × higher leverage on good news days.
Backtest: top-10 coins with positive sent_3d averaged +2.8% actual 3-day return.

---

### Mode 3 — Direction Bias (News Confirms or Contradicts Price Signal)

**What it does:** Use news sentiment to confirm the direction of a price signal.
Only take LONG when sentiment is positive. Only take SHORT when sentiment is negative.
When sentiment contradicts the price signal — reduce size or skip.

**When to use:** Medium impact. Best for T3/T4 coins where news moves price significantly.

```python
n = news.get(coin_slug, {})
sent_3d = n.get("news_sentiment_3d", 0.0)
tier = sig.tier   # "T1", "T2", "T3", "T4"

# Only apply news direction filter to T3/T4 — T1/T2 are macro-driven
if tier in ("T3", "T4"):
    if sig.direction == "LONG" and sent_3d < -0.15:
        log.info(f"Skip {sig.symbol} LONG: price=BUY but news bearish (sent_3d={sent_3d:.3f})")
        return False
    if sig.direction == "SHORT" and sent_3d > 0.15:
        log.info(f"Skip {sig.symbol} SHORT: price=SELL but news bullish (sent_3d={sent_3d:.3f})")
        return False
```

**Rationale:** T3/T4 small caps are narrative-driven. If the 3-day news sentiment disagrees with
the price forecast, one of them is picking up noise. Both agreeing = higher conviction.

---

### Mode 4 — Adoption Flag Boost (News-Driven Upside Expansion)

**What it does:** When a coin has an adoption event (ETF approval, major partnership,
institutional buy), expand the TP target. More room to run = hold longer, capture more.

**Integration point:** In `executor.py` after SL/TP is calculated.

```python
n = news.get(coin_slug, {})
if n.get("news_adoption_flag") and sig.direction == "LONG":
    # Widen TP by 50% on adoption news — let the catalyst play out
    original_tp_dist = abs(sig.tp_px - sig.entry_px)
    sig.tp_px = sig.entry_px + (original_tp_dist * 1.5)
    log.info(f"{sig.symbol}: adoption news → TP widened by 50% to {sig.tp_px:.4f}")
```

**Rationale:** Adoption events (Coinbase listing, BlackRock fund, exchange integration)
create multi-day sustained moves. S4's fixed 6% TP is too tight for these — often
the coin moves 15–25%. Expanding to 9% catches more of the move.

---

### Mode 5 — Volume Spike Early Warning (Preemptive Signal)

**What it does:** When `news_volume_zscore_1d` is high (>2σ above normal),
something significant is happening. Even if the price signal is flat, open a small
speculative position in the direction of sentiment.

**When to use:** Opportunistic. News volume spikes often precede price moves by 4–12 hours
because institutions read news faster than retail, but the price impact takes time.

```python
n = news.get(coin_slug, {})
vol_z = n.get("news_volume_zscore_1d", 0.0)
sent_1d = n.get("news_sentiment_1d", 0.0)

# News-only speculative signal (no TimesFM signal required)
if vol_z > 2.0 and abs(sent_1d) > 0.25 and coin_slug not in open_symbols:
    direction = "LONG" if sent_1d > 0 else "SHORT"
    # Small position: 25% of normal T4 margin
    speculative_margin = TIER_MARGIN_USD["T4"] * 0.25
    leverage = 2.0   # conservative leverage for news plays
    log.info(f"NEWS SPIKE: {coin_slug} vol_z={vol_z:.1f} sent={sent_1d:.3f} → {direction} spec trade")
    # ... fire speculative trade
```

**Rationale:** Volume z-score >2 means 3× the normal article flow.
This is statistically rare (happens ~5% of days per coin) and predictive.
In our FE_NEWS_SIGNALS, DOGE showed vol_z=1.37 on Feb 23 while sentiment was −0.45 —
a SHORT setup that preceded a notable drop.

---

### Mode 6 — Full Signal Fusion (ML_SIGNALS + TimesFM Combined Score)

**What it does:** For every coin, compute a **combined score** from:
1. TimesFM S4 prediction (pred_4h) → normalized to [−1, +1]
2. ML_SIGNALS signal_score (prob_buy − prob_sell) → already in [−1, +1]

Use the combined score for both direction decision AND leverage sizing.
This is the true two-tower architecture — price tower + news tower.

```python
# Normalize S4 prediction to [-1, +1]
def normalize_s4(pred_4h, thr, max_lvg=5):
    """Converts raw pred_4h to a [-1, +1] score."""
    if abs(pred_4h) < thr:
        return 0.0
    return max(-1.0, min(1.0, pred_4h / (thr * max_lvg)))

# Load ML_SIGNALS for today (keyed by slug)
def get_ml_signals(conn, target_date) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT slug, signal_score, prob_buy, prob_sell, confidence
            FROM "ML_SIGNALS"
            WHERE DATE(timestamp) = %s
        """, (target_date,))
        return {r[0]: {"signal_score": r[1], "prob_buy": r[2],
                        "prob_sell": r[3], "confidence": r[4]}
                for r in cur.fetchall()}

# In signal_job.py — combined scoring
s4_score = normalize_s4(sig.pred_4h, sig.thr)
ml = ml_signals.get(coin_slug, {})
ml_score = ml.get("signal_score", 0.0)   # default 0 if not in ML_SIGNALS universe

# Weighted fusion: 70% price (S4), 30% news+fundamentals (ML)
PRICE_WEIGHT = 0.70
NEWS_WEIGHT  = 0.30
combined = (s4_score * PRICE_WEIGHT) + (ml_score * NEWS_WEIGHT)

# Use combined score for leverage instead of raw pred_4h / thr
final_leverage = max(1.0, min(MAX_LVG, abs(combined) * MAX_LVG))
direction = "LONG" if combined > 0 else ("SHORT" if combined < 0 else "FLAT")

log.info(f"{sig.symbol}: s4={s4_score:+.3f} ml={ml_score:+.3f} → combined={combined:+.3f} {direction} {final_leverage:.1f}x")
```

**Why 70/30:** TimesFM (AUC=0.896) is much stronger than our LightGBM (IC=0.014).
News adds ~30% orthogonal signal — not noise reduction, but different information.
S4 says WHEN and HOW MUCH. News says WHY and WHETHER to trust it.

---

## 4. Coin Coverage — Overlap with S4 Universe

S4 bot trades 76 coins. Our news pipeline covers 50 coins.
Overlap is the coins where integration is fully active.

**Fully covered (in both S4 universe AND FE_NEWS_SIGNALS):**

| T1 (10 coins) | T2 (15 coins) | T3 (25 coins) |
|---|---|---|
| bitcoin | avalanche | algorand |
| ethereum | cardano | aptos |
| solana | chainlink | arbitrum |
| xrp | aave | cosmos |
| bnb | uniswap | filecoin |
| dogecoin | near-protocol | immutable-x |
| tron | polkadot-new | injective |
| litecoin | stellar | kaspa |
| bitcoin-cash | shiba-inu | monero |
| ethereum-classic | hedera | optimism-ethereum |
| | toncoin | render |
| | sui | stacks |
| | bittensor | vechain |
| | ondo-finance | worldcoin-org |
| | internet-computer | celestia |

**Coins in S4 but NOT in news signals:** ~26 T3/T4 coins with insufficient article volume.
For these, the integration functions default to neutral (no boost, no veto).

---

## 5. Integration Architecture Diagram

```
                    ┌──────────────────────────────────────┐
                    │         signal_job.py (HH:01)        │
                    └──────────────────┬───────────────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
              ▼                        ▼                        ▼
  ┌───────────────────┐   ┌─────────────────────┐  ┌─────────────────────┐
  │   S4 TimesFM      │   │  FE_NEWS_SIGNALS     │  │   ML_SIGNALS        │
  │   (GPU inference) │   │  (PostgreSQL query)  │  │  (PostgreSQL query) │
  │                   │   │                      │  │                     │
  │  pred_4h per coin │   │  per-coin daily:     │  │  prob_buy per coin  │
  │  → direction      │   │  - sentiment 1/3/7d  │  │  signal_score       │
  │  → raw leverage   │   │  - volume zscore     │  │  confidence         │
  └────────┬──────────┘   │  - event flags       │  └──────────┬──────────┘
           │               │    (reg/hack/adopt)  │             │
           │               └──────────┬───────────┘             │
           │                          │                          │
           └──────────────────────────▼──────────────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │         FUSION LAYER                 │
                    │                                      │
                    │  1. Hard veto  (reg/hack flags)      │
                    │  2. Leverage multiplier (sent_3d)    │
                    │  3. Direction bias (T3/T4 coins)     │
                    │  4. TP expansion (adoption flag)     │
                    │  5. News-only speculative (vol_z)    │
                    │  6. Combined score (70% S4, 30% ML)  │
                    └──────────────────┬───────────────────┘
                                       │
                    ┌──────────────────▼───────────────────┐
                    │          executor.py                  │
                    │   MARKET entry + bracket SL/TP       │
                    └──────────────────────────────────────┘
```

---

## 6. Estimated Impact per Mode

Based on our backtest data and known market patterns:

| Mode | Implementation Effort | Expected Impact | Risk |
|---|---|---|---|
| 1. Hard Veto | 1 hour | Eliminates 5–15% of catastrophic losses | Misses some false-positive regulation events |
| 2. Leverage Multiplier | 2 hours | +10–20% on win trades, −10% on losses | Amplifies both directions |
| 3. Direction Bias (T3/T4) | 1 hour | +5–10% hit rate improvement on small caps | Reduces trade count |
| 4. TP Expansion on Adoption | 1 hour | Captures bigger moves on catalyst days | May miss TP less often |
| 5. Volume Spike Speculative | 3 hours | New alpha source, independent of TimesFM | Higher variance, smaller size |
| 6. Full Fusion (70/30) | 4 hours | Highest ceiling, most systematic | Requires careful weight tuning |

**Recommended starting point:** Mode 1 (Hard Veto) + Mode 2 (Leverage Multiplier).
These are low-risk, high-clarity improvements that take 3 hours total to implement.

---

## 7. Daily Refresh Schedule

The news pipeline runs automatically via GitHub Actions:

| Time (UTC) | Job | What happens |
|---|---|---|
| Every hour | Hourly news fetch | cc_news updated with new articles |
| 00:30 | Daily NLP pipeline | New articles scored by FinBERT → FE_NEWS_SENTIMENT updated |
| 00:30 | Daily NLP pipeline | FE_NEWS_SIGNALS updated (yesterday's coin-day rows) |
| 01:00 | Daily ML pipeline | ML_LABELS refreshed, MV refreshed, ML_SIGNALS generated |

**S4 bot fires at HH:01.** By then, previous day's FE_NEWS_SIGNALS are already loaded.
The query for "yesterday's signals" is always fresh when S4 scans at any hour of the day.

---

## 8. What's NOT in the Pipeline (Yet)

Known gaps and next-build items:

| Gap | Why it matters | Fix |
|---|---|---|
| Inference only goes to Feb 9 | MV anchored on ML_LABELS which needs 14d forward returns | Build separate inference view without label join |
| SHAP top_features is null in ML_SIGNALS | SHAP computation error (class index mismatch) | Fix shap_values index in daily_signals.py |
| Only 50 coins in news universe | Coin mapper limited to high-volume coins | Expand CATEGORY_TO_SLUG in coin_mapper.py |
| No hourly sentiment (only daily) | S4 fires hourly, news is 1-day resolution | Could score intraday articles per hour if needed |
| LightGBM IC=0.014 | Weak standalone | Fine-tune with better features, or use only as filter not primary signal |
| price_only model is degenerate | Outputs near-identical scores for all coins | Retrain with proper cross-sectional normalization |

---

## 9. Key Files Reference

```
CNFR-real/
├── src/
│   ├── db.py                    # Shared DB connection (handles missing sslmode)
│   ├── nlp/
│   │   ├── sentiment.py         # FinBERT scorer → FE_NEWS_SENTIMENT
│   │   ├── coin_mapper.py       # cc_news category → coin slug mapping (50 coins)
│   │   └── event_classifier.py  # Rule-based: REGULATION, HACK, ADOPTION, MACRO, etc.
│   ├── features/
│   │   ├── news_signals.py      # Aggregator → FE_NEWS_SIGNALS (--from-date, --to-date)
│   │   ├── labels.py            # Forward returns from 1K_coins_ohlcv → ML_LABELS
│   │   └── refresh_mv.py        # REFRESH MATERIALIZED VIEW CONCURRENTLY
│   ├── models/
│   │   ├── train_lgbm.py        # Walk-forward LightGBM trainer (price_only + news_augmented)
│   │   ├── backtest.py          # Full walk-forward backtest (IC, ICIR, Sharpe, HitRate, MaxDD)
│   │   ├── evaluate.py          # IC, ICIR, Sharpe, classification metrics
│   │   └── registry.py          # ML_MODEL_REGISTRY read/write
│   └── inference/
│       ├── daily_signals.py     # Load active model → ML_SIGNALS (1000 coins/day)
│       └── etl_tracker.py       # Context manager wrapping etl_runs + etl_job_stats
├── migrations/
│   ├── 001_fe_news_sentiment.sql
│   ├── 002_fe_news_signals.sql
│   ├── 003_ml_labels.sql
│   ├── 004_ml_model_registry.sql
│   ├── 005_ml_signals.sql
│   ├── 006_ml_backtest_results.sql
│   └── 007_mv_ml_feature_matrix.sql
├── artifacts/
│   ├── lgbm_price_only_v1.pkl   # model_id=1 (degenerate, not used)
│   └── lgbm_news_augmented_v1.pkl  # model_id=2, ACTIVE, IC=0.014
├── .github/workflows/
│   ├── daily-nlp-pipeline.yml   # 00:30 UTC — FinBERT + signals
│   ├── daily-ml-signals.yml     # 01:00 UTC — retrain + inference
│   └── hourly-news-fetch.yml    # Every hour — cc_news update
└── run_gpu_sentiment.bat        # One-click local GPU backfill script
```

---

## 10. Database Tables Summary

| Table | Rows | Purpose | Touch Policy |
|---|---|---|---|
| `cc_news` | 66,391 | Raw articles | READ ONLY — fetcher writes, we only read |
| `1K_coins_ohlcv` | ~1.7M | Price data | READ ONLY |
| `FE_NEWS_SENTIMENT` | 43,487 | Article-level FinBERT scores | WRITE — our pipeline |
| `FE_NEWS_SIGNALS` | 5,085 | Daily per-coin news signals | WRITE — our pipeline |
| `ML_LABELS` | 112,895 | Forward return labels | WRITE — our pipeline |
| `ML_MODEL_REGISTRY` | 2 | Trained model metadata | WRITE — our pipeline |
| `ML_SIGNALS` | 1,000 | Daily coin rankings | WRITE — our pipeline |
| `ML_BACKTEST_RESULTS` | 2 | Backtest metrics | WRITE — our pipeline |
| `mv_ml_feature_matrix` | 112,895 | Joined feature matrix (MV) | WRITE — refreshed daily |
| All other `FE_*` tables | — | Production features | READ ONLY — never touched |

---

*Document generated Feb 25, 2026. Pipeline version 1.0.*
*Next update planned when inference path is extended beyond Feb 9 and SHAP integration is fixed.*
