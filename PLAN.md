# CryptoPrism — News Intelligence Integration Plan
**Created:** Feb 25, 2026
**Status:** Pipeline complete. Integration into S4/LIVE bot pending.

---

## What Exists Today

### News Pipeline (CNFR-real repo — COMPLETE)
| Component | Status | Rows / Notes |
|---|---|---|
| cc_news | ✅ Live | 66,391 articles, updated hourly |
| FE_NEWS_SENTIMENT | ✅ Live | 43,487 rows — FinBERT GPU scored |
| FE_NEWS_SIGNALS | ✅ Live | 5,085 coin-day rows, 50 coins × 128 days |
| ML_LABELS | ✅ Live | 112,895 rows — forward returns Oct 21 → Feb 9 |
| mv_ml_feature_matrix | ✅ Live | 112,895 rows — full feature join |
| lgbm_news_augmented_v1 | ✅ Trained | IC-3d=0.014, ICIR=0.35, HitRate=55.8% |
| ML_SIGNALS | ✅ Live | 1,000 coin rankings (Feb 9 latest) |
| ML_BACKTEST_RESULTS | ✅ Live | Both models backtested |
| GitHub Actions (daily NLP + ML) | ✅ Live | 00:30 + 01:00 UTC daily |

### Price Bots (cpio_google-research-timesfm repo — EXISTING)
| Bot | Status | Signal | Notes |
|---|---|---|---|
| S4 bot | ✅ Live (testnet) | Fine-tuned TimesFM 2.5 200M | AUC=0.896, F1=0.839, 4h hold |
| LIVE bot | ✅ Live (testnet) | 1h momentum + PSAR + CVaR | Backtest: +312% compounded 6mo |

---

## Integration Phases

---

### PHASE 1 — Hard Veto + Leverage Multiplier (3 hours)
**Priority: HIGH | Risk: LOW | Impact: Immediate**

Plug news signals directly into `s4_bot/jobs/signal_job.py`.
No model changes. No architecture changes. Pure filter layer.

#### Step 1.1 — Add PostgreSQL connection to S4 bot
- Add `psycopg2-binary` to `s4_bot/requirements.txt`
- Add DB env vars to `s4_bot/.env` (same creds as CNFR-real)
- Create `s4_bot/data/news_signals.py` — single function `get_news_signals(conn, date)`
  that returns `{ slug: { sent_1d, sent_3d, regulation_flag, security_flag, adoption_flag, vol_zscore } }`
- Create `s4_bot/utils/slug_map.py` — maps `"SOLUSDT"` → `"solana"` for all 76 S4 symbols

#### Step 1.2 — Hard Veto (regulation + security flags)
In `signal_job.py` inside `_try_trade()`, after cooldown check:
```python
n = news.get(slug, {})
if sig.direction == "LONG":
    if n.get("news_regulation_flag") and n.get("news_sentiment_3d", 0) < -0.1:
        log.info(f"VETO {sig.symbol}: regulation + negative news")
        return False
    if n.get("news_security_flag") and n.get("news_sentiment_1d", 0) < -0.2:
        log.info(f"VETO {sig.symbol}: security/hack event")
        return False
```

#### Step 1.3 — Leverage Multiplier (sentiment scaling)
After S4 computes `base_leverage`:
```python
sent_3d = n.get("news_sentiment_3d", 0.0)
news_mult = max(0.6, min(1.4, 1.0 + sent_3d * 0.3))
final_leverage = max(1.0, min(MAX_LVG, base_leverage * news_mult))
```

#### Step 1.4 — Test
- Run `python run_now.py` and check logs show news context per signal
- Confirm vetoes fire on coins with active flags
- Confirm leverage adjusts correctly vs baseline

**Deliverable:** S4 bot reads news before every trade decision.

---

### PHASE 2 — TP Expansion + Volume Spike (4 hours)
**Priority: MEDIUM | Risk: LOW-MEDIUM | Impact: Captures catalyst moves**

#### Step 2.1 — Adoption Flag TP Expansion
In `executor.py`, after SL/TP computed, before bracket orders placed:
```python
if n.get("news_adoption_flag") and sig.direction == "LONG":
    tp_dist = abs(sig.tp_px - sig.entry_px)
    sig.tp_px = sig.entry_px + (tp_dist * 1.5)   # widen by 50%
    log.info(f"{sig.symbol}: adoption event → TP widened to {sig.tp_px:.5f}")
```

#### Step 2.2 — News-Only Volume Spike Positions
New function `news_speculative_signals()` in `jobs/signal_job.py`:
- Query `FE_NEWS_SIGNALS` for coins with `vol_zscore > 2.0` AND `|sent_1d| > 0.25`
- Filter out coins already in open positions or cooldown
- Open T4-budget speculative positions at 2x leverage, 25% of normal T4 margin
- These fire AFTER the main S4 signal scan

**Deliverable:** Bot captures ETF/partnership catalyst moves and news-volume preemption.

---

### PHASE 3 — Full Signal Fusion (6 hours)
**Priority: MEDIUM | Risk: MEDIUM | Impact: Highest ceiling**

Two-tower architecture: TimesFM (price) + LightGBM (news+fundamentals).

#### Step 3.1 — Fix ML_SIGNALS inference path
Current issue: `mv_ml_feature_matrix` only goes to Feb 9 (anchored on ML_LABELS which needs 14d forward).
- Create `mv_inference_features` — same join as mv_ml_feature_matrix but WITHOUT ML_LABELS
- This lets inference run on today's date, not just labeled historical dates
- `daily_signals.py` switches to query `mv_inference_features` instead of `mv_ml_feature_matrix`

#### Step 3.2 — Load ML_SIGNALS in signal_job
```python
ml_signals = get_ml_signals(pg_conn, today)  # { slug: { signal_score, prob_buy, confidence } }
```

#### Step 3.3 — Compute combined score
```python
s4_norm = normalize_s4(sig.pred_4h, sig.thr)    # → [-1, +1]
ml_score = ml_signals.get(slug, {}).get("signal_score", 0.0)

PRICE_W, NEWS_W = 0.70, 0.30
combined = (s4_norm * PRICE_W) + (ml_score * NEWS_W)
final_leverage = max(1.0, min(MAX_LVG, abs(combined) * MAX_LVG))
```

#### Step 3.4 — Direction consistency check
If `combined` and `s4_norm` disagree in direction → reduce size by 50% (uncertainty signal).

**Deliverable:** Every S4 trade decision uses both price AND news intelligence.

---

### PHASE 4 — LIVE Bot Integration (3 hours)
**Priority: LOW-MEDIUM | Risk: LOW | Impact: Second bot benefits too**

Same Phases 1–3 but applied to `live_bot/jobs/signal_job.py`.
LIVE bot has dynamic PSAR-based SL/TP — adoption flag expansion works differently here:
- Instead of widening TP percentage, increase `PSAR_RR_MULT` from 2.5 → 3.5 on adoption events.

---

### PHASE 5 — Regime Filter (future)
**Priority: LOW | Risk: LOW | Impact: Bear market protection**

When `news_sentiment_7d < -0.2` for >70% of T1 coins simultaneously:
- Detect broad bear sentiment regime
- Cut `ALLOC_START` from 50% to 25%
- Suppress all T3/T4 LONG signals
- This is a market-wide news regime detector, not per-coin

**Deliverable:** Bot goes defensive when the whole market has sustained negative news.

---

## Known Issues to Fix Before Phase 1

| Issue | File | Fix |
|---|---|---|
| SHAP `top_features` is null in ML_SIGNALS | `src/inference/daily_signals.py` | Fix `sv[2]` indexing — use `model.classes_` to find BUY class index |
| ML_SIGNALS only has Feb 9 (inference gap) | `src/inference/daily_signals.py` | Build `mv_inference_features` view (Phase 3.1) |
| price_only model degenerate (IC=NaN) | Retrain | Add cross-sectional rank normalization before training |

---

## Symbol → Slug Mapping (needed for Phase 1)

S4 uses Binance symbols (`SOLUSDT`). DB uses slugs (`solana`).
This mapping needs to be built in `s4_bot/utils/slug_map.py`.

```python
SYMBOL_TO_SLUG = {
    "BTCUSDT":  "bitcoin",          "ETHUSDT":  "ethereum",
    "SOLUSDT":  "solana",           "XRPUSDT":  "xrp",
    "BNBUSDT":  "bnb",              "DOGEUSDT": "dogecoin",
    "TRXUSDT":  "tron",             "ADAUSDT":  "cardano",
    "LINKUSDT": "chainlink",        "AVAXUSDT": "avalanche",
    "BCHUSDT":  "bitcoin-cash",     "SUIUSDT":  "sui",
    "TONUSDT":  "toncoin",          "XLMUSDT":  "stellar",
    "SHIBUSDT": "shiba-inu",        "HBARUSDT": "hedera",
    "LTCUSDT":  "litecoin",         "DOTUSDT":  "polkadot-new",
    "XMRUSDT":  "monero",           "UNIUSDT":  "uniswap",
    "NEARUSDT": "near-protocol",    "APTUSDT":  "aptos",
    "AAVEUSDT": "aave",             "TAOUSDT":  "bittensor",
    "ICPUSDT":  "internet-computer","ETCUSDT":  "ethereum-classic",
    "KASUSDT":  "kaspa",            "ONDOUSDT": "ondo-finance",
    "ZECUSDT":  "zcash",            "RENDERUSDT":"render",
    "VETUSDT":  "vechain",          "ARBUSDT":  "arbitrum",
    "FILUSDT":  "filecoin",         "ATOMUSDT": "cosmos",
    "ALGOUSDT": "algorand",         "WLDUSDT":  "worldcoin-org",
    "STXUSDT":  "stacks",           "OPUSDT":   "optimism-ethereum",
    "CELRUSDT": "celestia",         "IMXUSDT":  "immutable-x",
    "INJUSDT":  "injective",        "CHZUSDT":  "chiliz",
    "FETUSDT":  "artificial-superintelligence-alliance",
    "BONKUSDT": "bonk1",            "MANTRAUSDT":"mantra",
    # T3/T4 without news coverage → default to None (no veto, no boost)
}
```

---

## Daily Operations (After Integration)

```
00:00 UTC  FE_* tables updated (existing production pipeline)
00:30 UTC  GitHub Actions: FinBERT scoring → FE_NEWS_SIGNALS updated
01:00 UTC  GitHub Actions: ML_SIGNALS updated (1,000 coin rankings)
HH:01 UTC  S4 bot fires:
           → fetch_ohlcv() for 76 coins
           → timesfm.forecast() → pred_4h
           → get_news_signals(pg_conn) → news context per coin
           → get_ml_signals(pg_conn)  → ML rankings per coin
           → fusion logic → final direction + leverage
           → execute trades
HH+5min    Monitor job: SL/TP/trailing (unchanged)
```

---

## File Locations

| What | Where |
|---|---|
| News pipeline repo | `C:/cpio_db/CNFR-real/` |
| S4 bot | `C:/cpio_db/cpio_google-research-timesfm/s4_bot/` |
| LIVE bot | `C:/cpio_db/cpio_google-research-timesfm/live_bot/` |
| TimesFM backtest scripts | `C:/cpio_db/cpio_google-research-timesfm/` |
| Full integration docs | `C:/cpio_db/CNFR-real/INTEGRATION_GUIDE.md` |
| This plan | `C:/cpio_db/CNFR-real/PLAN.md` |

---

## Start Here → Phase 1, Step 1.1
Build `s4_bot/data/news_signals.py` and `s4_bot/utils/slug_map.py`.
Everything else follows.
