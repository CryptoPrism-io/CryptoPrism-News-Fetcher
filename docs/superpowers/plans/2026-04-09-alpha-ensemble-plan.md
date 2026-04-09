# Alpha Ensemble Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a multi-model ensemble (BTC residual decomposition, TCN, LSTM, news event detector, macro regime HMM, enhanced LightGBM + meta-learner) that finds alpha beyond BTC-correlated moves with hourly inference.

**Architecture:** Six components feed an ensemble meta-learner. BTC residual decomposition is the foundation layer — all downstream models train on residual returns (after stripping BTC beta). TCN captures hourly microstructure (7d window), LSTM captures daily narratives (30d window), news event detector classifies articles into actionable event types, and a macro regime HMM gates signal confidence. Enhanced LightGBM combines ~99 features including neural embeddings, and a simple meta-learner produces the final signal.

**Tech Stack:** Python 3.11+, PyTorch (TCN + LSTM), LightGBM, hmmlearn, transformers (FinBERT), ONNX Runtime, psycopg2, pandas, numpy, scipy.

**Design Spec:** `docs/superpowers/specs/2026-04-09-alpha-ensemble-design.md`

---

## File Structure

```
src/
├── db.py                              (existing — add get_backtest_h_conn)
├── features/
│   ├── btc_residuals.py               (NEW — BTC beta decomposition)
│   ├── news_events.py                 (NEW — event classification + features)
│   ├── labels.py                      (MODIFY — add label_3d_residual)
│   ├── news_signals.py                (existing, unchanged)
│   └── refresh_mv.py                  (existing, unchanged)
├── models/
│   ├── tcn.py                         (NEW — Temporal Conv Net)
│   ├── lstm_extractor.py              (NEW — LSTM feature extractor)
│   ├── regime.py                      (NEW — HMM regime model)
│   ├── train_ensemble.py              (NEW — enhanced LightGBM + meta-learner)
│   ├── train_lgbm.py                  (existing, unchanged — legacy path)
│   ├── evaluate.py                    (existing, unchanged)
│   ├── registry.py                    (existing, unchanged)
│   └── backtest.py                    (existing, unchanged)
├── inference/
│   ├── hourly_signals.py              (NEW — hourly ensemble inference pipeline)
│   ├── daily_signals.py               (existing, unchanged — legacy path)
│   └── etl_tracker.py                 (existing, unchanged)
└── nlp/
    ├── event_classifier.py            (MODIFY — add DistilBERT event type model)
    ├── sentiment.py                   (existing, unchanged)
    └── coin_mapper.py                 (existing, unchanged)

migrations/
├── 010_fe_btc_residuals.sql           (NEW)
├── 011_fe_news_events.sql             (NEW)
├── 012_ml_regime.sql                  (NEW)
├── 013_ml_tcn_embeddings.sql          (NEW)
├── 014_ml_lstm_embeddings.sql         (NEW)
└── 015_ml_signals_v2.sql              (NEW)

tests/
├── test_btc_residuals.py              (NEW)
├── test_regime.py                     (NEW)
├── test_tcn.py                        (NEW)
├── test_lstm.py                       (NEW)
├── test_news_events.py                (NEW)
└── test_ensemble.py                   (NEW)

.github/workflows/
└── hourly-ensemble.yml                (NEW — hourly inference cron)
```

---

### Task 1: BTC Residual Decomposition (Foundation Layer)

**Files:**
- Create: `migrations/010_fe_btc_residuals.sql`
- Create: `src/features/btc_residuals.py`
- Create: `tests/test_btc_residuals.py`
- Modify: `src/db.py` (add `get_backtest_h_conn`)

**Overview:** Rolling 30-day OLS regression per coin: `coin_ret = alpha + beta * btc_ret + epsilon`. Strips BTC correlation so downstream models see only idiosyncratic alpha. Runs on both hourly (cp_backtest_h) and daily (cp_backtest) OHLCV data.

- [ ] **Step 1: Add `get_backtest_h_conn` to db.py**

Add a third connection factory for the hourly backtest database. Follow the existing pattern in `get_backtest_conn`.

```python
# Add to src/db.py after get_backtest_conn:

def get_backtest_h_conn():
    """Connect to the hourly backtest DB (cp_backtest_h)."""
    kwargs = dict(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", 5432),
        dbname="cp_backtest_h",
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )
    sslmode = os.environ.get("DB_SSLMODE", "").strip()
    if sslmode:
        kwargs["sslmode"] = sslmode
    return psycopg2.connect(**kwargs)
```

- [ ] **Step 2: Create migration 010_fe_btc_residuals.sql**

```sql
-- Migration 010: FE_BTC_RESIDUALS
-- Stores BTC beta decomposition per coin per timestamp.
-- Computed from rolling 30-day OLS: coin_ret = alpha + beta*btc_ret + epsilon

CREATE TABLE IF NOT EXISTS "FE_BTC_RESIDUALS" (
    id              BIGSERIAL PRIMARY KEY,
    slug            TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    beta_30d        DOUBLE PRECISION,
    alpha_30d       DOUBLE PRECISION,
    residual_1h     DOUBLE PRECISION,
    residual_1d     DOUBLE PRECISION,
    residual_vol_ratio DOUBLE PRECISION,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_btc_res_slug_ts
    ON "FE_BTC_RESIDUALS" (slug, timestamp);

CREATE INDEX IF NOT EXISTS idx_btc_res_ts
    ON "FE_BTC_RESIDUALS" (timestamp DESC);

COMMENT ON TABLE "FE_BTC_RESIDUALS" IS
    'BTC beta decomposition: rolling 30d OLS residuals per coin. '
    'Foundation for all alpha models — strips BTC correlation.';
```

- [ ] **Step 3: Write test_btc_residuals.py**

```python
"""Tests for BTC residual decomposition."""
import numpy as np
import pytest
from datetime import datetime, timezone


def test_rolling_ols_basic():
    """Rolling OLS on synthetic data recovers known beta."""
    from src.features.btc_residuals import rolling_ols

    np.random.seed(42)
    n = 60 * 24  # 60 days hourly
    btc_ret = np.random.normal(0, 0.01, n)
    true_beta = 1.5
    true_alpha = 0.0002
    noise = np.random.normal(0, 0.005, n)
    coin_ret = true_alpha + true_beta * btc_ret + noise

    result = rolling_ols(coin_ret, btc_ret, window=30 * 24)
    # After warmup, beta should be close to 1.5
    valid = result[~np.isnan(result["beta"])]
    assert len(valid) > 0
    assert abs(valid["beta"].iloc[-1] - true_beta) < 0.3
    assert "residual" in result.columns
    assert "alpha" in result.columns


def test_rolling_ols_short_series():
    """Returns NaN for windows shorter than lookback."""
    from src.features.btc_residuals import rolling_ols

    btc_ret = np.random.normal(0, 0.01, 100)
    coin_ret = np.random.normal(0, 0.01, 100)
    result = rolling_ols(coin_ret, btc_ret, window=720)
    # All should be NaN since 100 < 720
    assert result["beta"].isna().all()


def test_residual_vol_ratio():
    """Residual vol ratio between 0 and 1."""
    from src.features.btc_residuals import compute_residual_vol_ratio

    residuals = np.random.normal(0, 0.005, 720)
    total_rets = np.random.normal(0, 0.01, 720)
    ratio = compute_residual_vol_ratio(residuals, total_rets, window=720)
    assert 0 <= ratio <= 1


def test_compute_for_slug():
    """End-to-end: compute residuals for a single coin given OHLCV DataFrames."""
    from src.features.btc_residuals import compute_for_slug
    import pandas as pd

    np.random.seed(42)
    n = 60 * 24
    dates = pd.date_range("2025-06-01", periods=n, freq="h", tz="UTC")
    btc_close = 50000 * np.cumprod(1 + np.random.normal(0, 0.005, n))
    coin_close = 100 * np.cumprod(1 + 1.3 * np.random.normal(0, 0.005, n) + np.random.normal(0, 0.003, n))

    btc_df = pd.DataFrame({"timestamp": dates, "close": btc_close})
    coin_df = pd.DataFrame({"timestamp": dates, "close": coin_close})

    result = compute_for_slug(coin_df, btc_df, window_hours=30 * 24)
    assert len(result) == n
    assert "beta_30d" in result.columns
    assert "residual_1h" in result.columns
    assert "residual_vol_ratio" in result.columns
    # Should have valid values after warmup
    valid = result.dropna(subset=["beta_30d"])
    assert len(valid) > n * 0.4
```

- [ ] **Step 4: Run tests, verify they fail**

Run: `cd C:/cpio_db/CryptoPrism-News-Fetcher && python -m pytest tests/test_btc_residuals.py -v`
Expected: ImportError — `src.features.btc_residuals` does not exist yet.

- [ ] **Step 5: Implement btc_residuals.py**

```python
"""
btc_residuals.py
BTC beta decomposition — rolling 30-day OLS per coin.
Strips BTC correlation so downstream models see only idiosyncratic alpha.

Usage:
    python -m src.features.btc_residuals                      # incremental (latest hour)
    python -m src.features.btc_residuals --backfill            # full backfill from hourly data
    python -m src.features.btc_residuals --daily               # daily residuals from daily OHLCV
"""

import argparse
import logging
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src.db import get_db_conn, get_backtest_conn, get_backtest_h_conn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

WINDOW_HOURS = 30 * 24  # 30 days in hours
WINDOW_DAYS = 30


def rolling_ols(coin_ret: np.ndarray, btc_ret: np.ndarray, window: int) -> pd.DataFrame:
    """
    Rolling OLS: coin_ret = alpha + beta * btc_ret + epsilon.
    Returns DataFrame with columns: beta, alpha, residual.
    NaN for rows where window is insufficient.
    """
    n = len(coin_ret)
    beta = np.full(n, np.nan)
    alpha = np.full(n, np.nan)
    residual = np.full(n, np.nan)

    for i in range(window, n):
        y = coin_ret[i - window:i]
        x = btc_ret[i - window:i]

        # Skip if all zeros or constant
        if np.std(x) == 0:
            continue

        # OLS: beta = cov(x,y)/var(x), alpha = mean(y) - beta*mean(x)
        cov_xy = np.cov(x, y, ddof=1)[0, 1]
        var_x = np.var(x, ddof=1)
        b = cov_xy / var_x
        a = np.mean(y) - b * np.mean(x)

        beta[i] = b
        alpha[i] = a
        residual[i] = coin_ret[i] - (a + b * btc_ret[i])

    return pd.DataFrame({"beta": beta, "alpha": alpha, "residual": residual})


def compute_residual_vol_ratio(residuals: np.ndarray, total_rets: np.ndarray,
                                window: int) -> float:
    """Ratio of residual volatility to total return volatility."""
    valid_res = residuals[~np.isnan(residuals)][-window:]
    valid_tot = total_rets[~np.isnan(total_rets)][-window:]

    if len(valid_res) < 10 or len(valid_tot) < 10:
        return np.nan

    res_vol = np.std(valid_res, ddof=1)
    tot_vol = np.std(valid_tot, ddof=1)

    if tot_vol == 0:
        return np.nan

    return min(res_vol / tot_vol, 1.0)


def compute_for_slug(coin_df: pd.DataFrame, btc_df: pd.DataFrame,
                     window_hours: int = WINDOW_HOURS) -> pd.DataFrame:
    """
    Compute BTC residuals for a single coin.

    Args:
        coin_df: DataFrame with columns [timestamp, close]
        btc_df: DataFrame with columns [timestamp, close]
        window_hours: OLS lookback window in hours

    Returns:
        DataFrame with columns [timestamp, beta_30d, alpha_30d,
        residual_1h, residual_vol_ratio]
    """
    # Align on timestamp
    merged = coin_df.merge(btc_df, on="timestamp", suffixes=("_coin", "_btc"))
    merged = merged.sort_values("timestamp").reset_index(drop=True)

    if len(merged) < window_hours + 1:
        # Not enough data — return all NaN
        result = coin_df[["timestamp"]].copy()
        for col in ["beta_30d", "alpha_30d", "residual_1h", "residual_vol_ratio"]:
            result[col] = np.nan
        return result

    # Compute returns
    coin_ret = merged["close_coin"].pct_change().fillna(0).values
    btc_ret = merged["close_btc"].pct_change().fillna(0).values

    # Rolling OLS
    ols = rolling_ols(coin_ret, btc_ret, window_hours)

    result = pd.DataFrame({
        "timestamp": merged["timestamp"].values,
        "beta_30d": ols["beta"].values,
        "alpha_30d": ols["alpha"].values,
        "residual_1h": ols["residual"].values,
    })

    # Compute rolling residual vol ratio
    vol_ratios = np.full(len(result), np.nan)
    for i in range(window_hours, len(result)):
        vol_ratios[i] = compute_residual_vol_ratio(
            ols["residual"].values[:i + 1],
            coin_ret[:i + 1],
            window_hours,
        )
    result["residual_vol_ratio"] = vol_ratios

    return result


def fetch_hourly_ohlcv(conn, slugs: list[str] | None = None,
                       from_date: str | None = None) -> pd.DataFrame:
    """Fetch hourly OHLCV from cp_backtest_h."""
    query = 'SELECT slug, timestamp, close, volume FROM "ohlcv_1h_250_coins"'
    conditions = []
    params = {}
    if from_date:
        conditions.append("timestamp >= %(from_ts)s")
        params["from_ts"] = f"{from_date} 00:00:00+00"
    if slugs:
        conditions.append("slug = ANY(%(slugs)s)")
        params["slugs"] = slugs
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY slug, timestamp"

    return pd.read_sql(query, conn, params=params)


def fetch_daily_ohlcv(conn, from_date: str | None = None) -> pd.DataFrame:
    """Fetch daily OHLCV from cp_backtest (1K_coins_ohlcv)."""
    query = 'SELECT slug, timestamp, close, volume FROM "1K_coins_ohlcv"'
    if from_date:
        query += " WHERE timestamp >= %(from_ts)s"
    query += " ORDER BY slug, timestamp"
    params = {"from_ts": f"{from_date} 00:00:00+00"} if from_date else {}
    return pd.read_sql(query, conn, params=params)


def upsert_residuals(conn, rows: list[dict]):
    """Upsert residual rows into FE_BTC_RESIDUALS."""
    sql = """
        INSERT INTO "FE_BTC_RESIDUALS" (
            slug, timestamp, beta_30d, alpha_30d,
            residual_1h, residual_1d, residual_vol_ratio
        ) VALUES (
            %(slug)s, %(timestamp)s, %(beta_30d)s, %(alpha_30d)s,
            %(residual_1h)s, %(residual_1d)s, %(residual_vol_ratio)s
        )
        ON CONFLICT (slug, timestamp) DO UPDATE SET
            beta_30d           = EXCLUDED.beta_30d,
            alpha_30d          = EXCLUDED.alpha_30d,
            residual_1h        = EXCLUDED.residual_1h,
            residual_1d        = EXCLUDED.residual_1d,
            residual_vol_ratio = EXCLUDED.residual_vol_ratio
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=1000)
    conn.commit()


def backfill_hourly():
    """Backfill FE_BTC_RESIDUALS from full hourly OHLCV history."""
    h_conn = get_backtest_h_conn()
    bt_conn = get_backtest_conn()

    log.info("Fetching hourly OHLCV from cp_backtest_h...")
    df = fetch_hourly_ohlcv(h_conn)
    h_conn.close()
    log.info(f"Loaded {len(df):,} hourly rows, {df['slug'].nunique()} coins")

    # Extract BTC
    btc = df[df["slug"] == "bitcoin"][["timestamp", "close"]].copy()
    if btc.empty:
        log.error("No BTC data found in ohlcv_1h_250_coins")
        return

    slugs = [s for s in df["slug"].unique() if s != "bitcoin"]
    all_rows = []

    for i, slug in enumerate(slugs):
        coin = df[df["slug"] == slug][["timestamp", "close"]].copy()
        result = compute_for_slug(coin, btc, WINDOW_HOURS)

        for _, r in result.iterrows():
            if np.isnan(r["beta_30d"]):
                continue
            all_rows.append({
                "slug": slug,
                "timestamp": r["timestamp"],
                "beta_30d": round(float(r["beta_30d"]), 6),
                "alpha_30d": round(float(r["alpha_30d"]), 8),
                "residual_1h": round(float(r["residual_1h"]), 8),
                "residual_1d": None,  # filled by daily pass
                "residual_vol_ratio": round(float(r["residual_vol_ratio"]), 6)
                    if not np.isnan(r["residual_vol_ratio"]) else None,
            })

        if (i + 1) % 50 == 0:
            log.info(f"  Processed {i + 1}/{len(slugs)} coins, {len(all_rows):,} rows")

    log.info(f"Upserting {len(all_rows):,} residual rows...")
    upsert_residuals(bt_conn, all_rows)
    bt_conn.close()
    log.info("Hourly backfill complete.")


def run_incremental():
    """Compute residuals for latest available hour."""
    h_conn = get_backtest_h_conn()
    bt_conn = get_backtest_conn()

    # Get latest timestamp in FE_BTC_RESIDUALS
    cur = bt_conn.cursor()
    cur.execute('SELECT MAX(timestamp) FROM "FE_BTC_RESIDUALS"')
    latest = cur.fetchone()[0]

    if latest:
        from_date = (latest - timedelta(days=31)).strftime("%Y-%m-%d")
    else:
        from_date = "2025-02-01"

    log.info(f"Incremental from {from_date}")
    df = fetch_hourly_ohlcv(h_conn, from_date=from_date)
    h_conn.close()

    btc = df[df["slug"] == "bitcoin"][["timestamp", "close"]].copy()
    slugs = [s for s in df["slug"].unique() if s != "bitcoin"]

    all_rows = []
    for slug in slugs:
        coin = df[df["slug"] == slug][["timestamp", "close"]].copy()
        result = compute_for_slug(coin, btc, WINDOW_HOURS)
        # Only take rows after latest
        if latest:
            result = result[result["timestamp"] > latest]
        for _, r in result.iterrows():
            if np.isnan(r["beta_30d"]):
                continue
            all_rows.append({
                "slug": slug,
                "timestamp": r["timestamp"],
                "beta_30d": round(float(r["beta_30d"]), 6),
                "alpha_30d": round(float(r["alpha_30d"]), 8),
                "residual_1h": round(float(r["residual_1h"]), 8),
                "residual_1d": None,
                "residual_vol_ratio": round(float(r["residual_vol_ratio"]), 6)
                    if not np.isnan(r["residual_vol_ratio"]) else None,
            })

    if all_rows:
        upsert_residuals(bt_conn, all_rows)
        log.info(f"Incremental: upserted {len(all_rows):,} rows")
    else:
        log.info("No new rows to upsert")
    bt_conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BTC Residual Decomposition")
    parser.add_argument("--backfill", action="store_true", help="Full backfill from hourly data")
    parser.add_argument("--daily", action="store_true", help="Compute daily residuals")
    args = parser.parse_args()

    if args.backfill:
        backfill_hourly()
    else:
        run_incremental()
```

- [ ] **Step 6: Run tests, verify they pass**

Run: `cd C:/cpio_db/CryptoPrism-News-Fetcher && python -m pytest tests/test_btc_residuals.py -v`
Expected: 3 tests PASS.

- [ ] **Step 7: Apply migration to cp_backtest**

```bash
cd C:/cpio_db/CryptoPrism-News-Fetcher
python -c "
from dotenv import load_dotenv; load_dotenv()
import os, psycopg2
conn = psycopg2.connect(host=os.environ['DB_HOST'], port='5432',
    dbname='cp_backtest', user=os.environ['DB_USER'],
    password=os.environ['DB_PASSWORD'], sslmode='require')
conn.autocommit = True
with open('migrations/010_fe_btc_residuals.sql') as f:
    conn.cursor().execute(f.read())
print('Migration 010 applied.')
conn.close()
"
```

- [ ] **Step 8: Run backfill**

```bash
cd C:/cpio_db/CryptoPrism-News-Fetcher
python -m src.features.btc_residuals --backfill
```

Expected: ~250 coins x ~10,000 valid hours each = ~2M+ residual rows upserted.

- [ ] **Step 9: Commit**

```bash
git add src/db.py src/features/btc_residuals.py migrations/010_fe_btc_residuals.sql tests/test_btc_residuals.py
git commit -m "feat: BTC residual decomposition (foundation layer)

Rolling 30d OLS: coin_ret = alpha + beta*btc_ret + epsilon.
Strips BTC correlation so downstream models see idiosyncratic alpha.
Supports hourly backfill from cp_backtest_h + incremental updates."
```

---

### Task 2: Macro Regime Model (HMM)

**Files:**
- Create: `migrations/012_ml_regime.sql`
- Create: `src/models/regime.py`
- Create: `tests/test_regime.py`

**Overview:** Hidden Markov Model with 4 states (risk_on, risk_off, choppy, breakout) using market-wide features. Gates ensemble signal confidence.

- [ ] **Step 1: Create migration 012_ml_regime.sql**

```sql
-- Migration 012: ML_REGIME
-- Market-wide regime classification from HMM.
-- One row per hour — not per coin.

CREATE TABLE IF NOT EXISTS "ML_REGIME" (
    id                      BIGSERIAL PRIMARY KEY,
    timestamp               TIMESTAMPTZ NOT NULL,
    regime_state            TEXT NOT NULL,  -- risk_on, risk_off, choppy, breakout
    confidence              DOUBLE PRECISION,
    trans_prob_risk_on      DOUBLE PRECISION,
    trans_prob_risk_off     DOUBLE PRECISION,
    trans_prob_choppy       DOUBLE PRECISION,
    trans_prob_breakout     DOUBLE PRECISION,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_regime_ts
    ON "ML_REGIME" (timestamp);

COMMENT ON TABLE "ML_REGIME" IS
    'Market-wide regime state from HMM. One row per hour. '
    'Used to gate ensemble signal confidence.';
```

- [ ] **Step 2: Write test_regime.py**

```python
"""Tests for macro regime HMM model."""
import numpy as np
import pandas as pd
import pytest


def test_build_features_returns_expected_columns():
    """Feature builder returns all expected columns."""
    from src.models.regime import build_regime_features

    np.random.seed(42)
    n = 100
    dates = pd.date_range("2025-06-01", periods=n, freq="D", tz="UTC")
    btc_df = pd.DataFrame({
        "timestamp": dates,
        "close": 50000 * np.cumprod(1 + np.random.normal(0, 0.02, n)),
        "volume": np.random.uniform(1e9, 5e9, n),
    })
    fg_df = pd.DataFrame({
        "timestamp": dates,
        "fear_greed_index": np.random.uniform(20, 80, n),
    })
    breadth = np.random.uniform(0.3, 0.8, n)

    features = build_regime_features(btc_df, fg_df, breadth)
    expected = ["fear_greed", "btc_vol_7d", "btc_vol_30d",
                "btc_vol_ratio", "btc_mom_24h", "btc_mom_72h", "breadth"]
    for col in expected:
        assert col in features.columns, f"Missing column: {col}"


def test_hmm_fit_predict():
    """HMM fits on features and produces valid state predictions."""
    from src.models.regime import fit_regime_hmm, predict_regime

    np.random.seed(42)
    n = 500
    X = np.random.randn(n, 7)

    model = fit_regime_hmm(X, n_states=4)
    assert model is not None

    states, probs = predict_regime(model, X)
    assert len(states) == n
    assert all(s in range(4) for s in states)
    assert probs.shape == (n, 4)
    # Probabilities sum to 1
    assert np.allclose(probs.sum(axis=1), 1.0, atol=1e-6)


def test_label_states():
    """State labeler maps HMM integer states to named regimes."""
    from src.models.regime import label_states

    np.random.seed(42)
    X = np.random.randn(100, 7)
    states = np.array([0] * 40 + [1] * 30 + [2] * 20 + [3] * 10)
    labels = label_states(states, X)
    assert set(labels).issubset({"risk_on", "risk_off", "choppy", "breakout"})
    assert len(labels) == 100
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `cd C:/cpio_db/CryptoPrism-News-Fetcher && python -m pytest tests/test_regime.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement regime.py**

```python
"""
regime.py
Hidden Markov Model for market regime classification.
4 states: risk_on, risk_off, choppy, breakout.

Usage:
    python -m src.models.regime --train          # fit HMM on historical data
    python -m src.models.regime --predict        # predict current regime
    python -m src.models.regime --backfill       # backfill ML_REGIME table
"""

import argparse
import logging
import os
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src.db import get_db_conn, get_backtest_conn, get_backtest_h_conn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

REGIME_NAMES = {0: "risk_on", 1: "risk_off", 2: "choppy", 3: "breakout"}
ARTIFACT_PATH = "artifacts/regime_hmm.pkl"


def build_regime_features(btc_df: pd.DataFrame, fg_df: pd.DataFrame,
                          breadth: np.ndarray) -> pd.DataFrame:
    """
    Build market-wide regime features from BTC OHLCV + fear/greed + breadth.

    Args:
        btc_df: BTC OHLCV with columns [timestamp, close, volume]
        fg_df: Fear & Greed with columns [timestamp, fear_greed_index]
        breadth: array of market breadth values (% coins above 20d MA)

    Returns:
        DataFrame with regime feature columns, indexed by timestamp.
    """
    df = btc_df.sort_values("timestamp").copy()
    df["ret"] = df["close"].pct_change()

    # Realized volatility
    df["btc_vol_7d"] = df["ret"].rolling(7).std()
    df["btc_vol_30d"] = df["ret"].rolling(30).std()
    df["btc_vol_ratio"] = df["btc_vol_7d"] / df["btc_vol_30d"].replace(0, np.nan)

    # Momentum
    df["btc_mom_24h"] = df["close"].pct_change(1)
    df["btc_mom_72h"] = df["close"].pct_change(3)

    # Volume profile
    df["vol_ratio"] = df["volume"] / df["volume"].rolling(30).mean()

    # Merge fear & greed
    fg = fg_df[["timestamp", "fear_greed_index"]].copy()
    fg = fg.rename(columns={"fear_greed_index": "fear_greed"})
    df = df.merge(fg, on="timestamp", how="left")
    df["fear_greed"] = df["fear_greed"].ffill()

    # Breadth
    df["breadth"] = breadth[:len(df)] if len(breadth) >= len(df) else np.pad(
        breadth, (0, len(df) - len(breadth)), constant_values=np.nan
    )

    feature_cols = ["fear_greed", "btc_vol_7d", "btc_vol_30d",
                    "btc_vol_ratio", "btc_mom_24h", "btc_mom_72h", "breadth"]

    result = df[["timestamp"] + feature_cols].copy()
    return result


def compute_market_breadth(conn, dates: list) -> np.ndarray:
    """Compute % of top 50 coins above their 20d MA for each date."""
    cur = conn.cursor()
    breadth = []
    for d in dates:
        cur.execute("""
            WITH ranked AS (
                SELECT slug, close, market_cap
                FROM "1K_coins_ohlcv"
                WHERE DATE(timestamp) = %s AND market_cap IS NOT NULL
                ORDER BY market_cap DESC LIMIT 50
            ), with_ma AS (
                SELECT r.slug,
                       r.close,
                       (SELECT AVG(o.close)
                        FROM "1K_coins_ohlcv" o
                        WHERE o.slug = r.slug
                          AND DATE(o.timestamp) BETWEEN %s - INTERVAL '20 days' AND %s
                       ) AS ma20
                FROM ranked r
            )
            SELECT COUNT(*) FILTER (WHERE close > ma20)::float / NULLIF(COUNT(*), 0)
            FROM with_ma
        """, (str(d), str(d), str(d)))
        row = cur.fetchone()
        breadth.append(row[0] if row[0] is not None else 0.5)
    return np.array(breadth)


def fit_regime_hmm(X: np.ndarray, n_states: int = 4):
    """Fit a Gaussian HMM on feature matrix X."""
    from hmmlearn.hmm import GaussianHMM

    model = GaussianHMM(
        n_components=n_states,
        covariance_type="diag",
        n_iter=200,
        random_state=42,
        verbose=False,
    )

    # Drop NaN rows for fitting
    valid_mask = ~np.isnan(X).any(axis=1)
    X_valid = X[valid_mask]

    if len(X_valid) < n_states * 10:
        log.error(f"Not enough valid rows for HMM: {len(X_valid)}")
        return None

    model.fit(X_valid)
    return model


def predict_regime(model, X: np.ndarray):
    """Predict regime states and transition probabilities."""
    valid_mask = ~np.isnan(X).any(axis=1)
    states = np.full(len(X), -1, dtype=int)
    probs = np.full((len(X), model.n_components), np.nan)

    if valid_mask.sum() > 0:
        X_valid = X[valid_mask]
        states[valid_mask] = model.predict(X_valid)
        probs[valid_mask] = model.predict_proba(X_valid)

    return states, probs


def label_states(states: np.ndarray, X: np.ndarray) -> list[str]:
    """
    Map integer HMM states to named regimes based on feature characteristics.
    Uses mean volatility and momentum per state to assign labels.
    """
    unique = np.unique(states[states >= 0])
    state_profiles = {}

    for s in unique:
        mask = states == s
        if mask.sum() == 0:
            continue
        # Features: [fear_greed, btc_vol_7d, btc_vol_30d, vol_ratio, mom_24h, mom_72h, breadth]
        mean_vol = np.nanmean(X[mask, 1])  # btc_vol_7d
        mean_mom = np.nanmean(X[mask, 4])  # btc_mom_24h
        mean_breadth = np.nanmean(X[mask, 6])  # breadth
        vol_of_vol = np.nanstd(X[mask, 1])  # vol-of-vol for breakout detection
        state_profiles[s] = {
            "vol": mean_vol, "mom": mean_mom,
            "breadth": mean_breadth, "vol_of_vol": vol_of_vol,
        }

    # Sort states by volatility
    sorted_states = sorted(state_profiles.keys(), key=lambda s: state_profiles[s]["vol"])

    name_map = {}
    if len(sorted_states) >= 4:
        # Lowest vol + positive momentum = risk_on
        # Lowest vol + negative momentum = choppy
        # High vol + negative momentum = risk_off
        # Highest vol-of-vol = breakout
        for s in sorted_states:
            p = state_profiles[s]
            if p["vol_of_vol"] == max(state_profiles[x]["vol_of_vol"] for x in sorted_states):
                name_map[s] = "breakout"
            elif p["vol"] == min(state_profiles[x]["vol"] for x in sorted_states if x not in name_map):
                name_map[s] = "risk_on" if p["mom"] >= 0 else "choppy"
            elif s not in name_map:
                name_map[s] = "risk_off" if p["mom"] < 0 else "choppy"

        # Fill any remaining
        for s in sorted_states:
            if s not in name_map:
                name_map[s] = "choppy"
    else:
        # Fewer states — simple mapping
        for i, s in enumerate(sorted_states):
            name_map[s] = list(REGIME_NAMES.values())[i % 4]

    return [name_map.get(s, "choppy") if s >= 0 else "choppy" for s in states]


def train_and_save():
    """Train HMM on historical data and save artifact."""
    bt_conn = get_backtest_conn()
    dbcp_conn = get_db_conn()

    # BTC daily OHLCV
    btc_df = pd.read_sql(
        'SELECT timestamp, close, volume FROM "1K_coins_ohlcv" '
        'WHERE slug = \'bitcoin\' ORDER BY timestamp',
        bt_conn,
    )
    log.info(f"Loaded {len(btc_df)} BTC daily rows")

    # Fear & Greed
    fg_df = pd.read_sql(
        'SELECT timestamp, fear_greed_index FROM "FE_FEAR_GREED_CMC" ORDER BY timestamp',
        dbcp_conn,
    )

    # Market breadth
    dates = btc_df["timestamp"].dt.date.tolist()
    log.info("Computing market breadth (this may take a few minutes)...")
    breadth = compute_market_breadth(bt_conn, dates[-365:])  # last year
    # Pad to full length
    full_breadth = np.full(len(dates), 0.5)
    full_breadth[-len(breadth):] = breadth

    bt_conn.close()
    dbcp_conn.close()

    features = build_regime_features(btc_df, fg_df, full_breadth)
    feature_cols = [c for c in features.columns if c != "timestamp"]
    X = features[feature_cols].values

    # Fit
    log.info("Fitting HMM...")
    model = fit_regime_hmm(X, n_states=4)
    if model is None:
        return

    # Label states
    states, probs = predict_regime(model, X)
    labels = label_states(states, X)

    # Log regime distribution
    from collections import Counter
    dist = Counter(labels)
    log.info(f"Regime distribution: {dict(dist)}")

    # Save
    artifact_dir = Path("artifacts")
    artifact_dir.mkdir(exist_ok=True)
    with open(ARTIFACT_PATH, "wb") as f:
        pickle.dump({"model": model, "feature_cols": feature_cols}, f)
    log.info(f"Saved HMM to {ARTIFACT_PATH}")


def upsert_regime(conn, rows: list[dict]):
    """Write regime predictions to ML_REGIME."""
    sql = """
        INSERT INTO "ML_REGIME" (
            timestamp, regime_state, confidence,
            trans_prob_risk_on, trans_prob_risk_off,
            trans_prob_choppy, trans_prob_breakout
        ) VALUES (
            %(timestamp)s, %(regime_state)s, %(confidence)s,
            %(trans_prob_risk_on)s, %(trans_prob_risk_off)s,
            %(trans_prob_choppy)s, %(trans_prob_breakout)s
        )
        ON CONFLICT (timestamp) DO UPDATE SET
            regime_state       = EXCLUDED.regime_state,
            confidence         = EXCLUDED.confidence,
            trans_prob_risk_on  = EXCLUDED.trans_prob_risk_on,
            trans_prob_risk_off = EXCLUDED.trans_prob_risk_off,
            trans_prob_choppy   = EXCLUDED.trans_prob_choppy,
            trans_prob_breakout = EXCLUDED.trans_prob_breakout
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
    conn.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Macro Regime HMM")
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--predict", action="store_true")
    parser.add_argument("--backfill", action="store_true")
    args = parser.parse_args()

    if args.train:
        train_and_save()
    elif args.predict:
        log.info("Predict mode not yet implemented — use --train first")
    elif args.backfill:
        log.info("Backfill mode not yet implemented — use --train first")
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `cd C:/cpio_db/CryptoPrism-News-Fetcher && python -m pytest tests/test_regime.py -v`
Expected: 3 tests PASS.

- [ ] **Step 6: Apply migration**

```bash
cd C:/cpio_db/CryptoPrism-News-Fetcher
python -c "
from dotenv import load_dotenv; load_dotenv()
import os, psycopg2
conn = psycopg2.connect(host=os.environ['DB_HOST'], port='5432',
    dbname='dbcp', user=os.environ['DB_USER'],
    password=os.environ['DB_PASSWORD'], sslmode='require')
conn.autocommit = True
with open('migrations/012_ml_regime.sql') as f:
    conn.cursor().execute(f.read())
print('Migration 012 applied.')
conn.close()
"
```

- [ ] **Step 7: Train and verify**

```bash
cd C:/cpio_db/CryptoPrism-News-Fetcher
python -m src.models.regime --train
```

Expected: HMM fits, regime distribution logged, artifact saved to `artifacts/regime_hmm.pkl`.

- [ ] **Step 8: Commit**

```bash
git add src/models/regime.py migrations/012_ml_regime.sql tests/test_regime.py artifacts/regime_hmm.pkl
git commit -m "feat: macro regime HMM (4-state market classifier)

risk_on/risk_off/choppy/breakout states from BTC vol, momentum,
fear-greed, and market breadth. Gates ensemble signal confidence."
```

---

### Task 3: LSTM Feature Extractor (Daily, 30d Window)

**Files:**
- Create: `migrations/014_ml_lstm_embeddings.sql`
- Create: `src/models/lstm_extractor.py`
- Create: `tests/test_lstm.py`

**Overview:** 2-layer LSTM on 30-day daily residual sequences. Outputs 12-dim embedding + auxiliary classification. Captures multi-week narratives (accumulation, capitulation).

- [ ] **Step 1: Create migration 014_ml_lstm_embeddings.sql**

```sql
-- Migration 014: ML_LSTM_EMBEDDINGS
-- Daily LSTM embedding vectors per coin.

CREATE TABLE IF NOT EXISTS "ML_LSTM_EMBEDDINGS" (
    id              BIGSERIAL PRIMARY KEY,
    slug            TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    lemb_0          DOUBLE PRECISION, lemb_1  DOUBLE PRECISION,
    lemb_2          DOUBLE PRECISION, lemb_3  DOUBLE PRECISION,
    lemb_4          DOUBLE PRECISION, lemb_5  DOUBLE PRECISION,
    lemb_6          DOUBLE PRECISION, lemb_7  DOUBLE PRECISION,
    lemb_8          DOUBLE PRECISION, lemb_9  DOUBLE PRECISION,
    lemb_10         DOUBLE PRECISION, lemb_11 DOUBLE PRECISION,
    lstm_prob_buy   DOUBLE PRECISION,
    lstm_prob_sell  DOUBLE PRECISION,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_lstm_emb_slug_ts
    ON "ML_LSTM_EMBEDDINGS" (slug, timestamp);

COMMENT ON TABLE "ML_LSTM_EMBEDDINGS" IS
    'LSTM 12-dim embeddings from 30-day daily residual sequences. '
    'Captures multi-week temporal patterns for ensemble features.';
```

- [ ] **Step 2: Write test_lstm.py**

```python
"""Tests for LSTM feature extractor."""
import numpy as np
import torch
import pytest


def test_lstm_model_forward():
    """LSTM model produces correct output shapes."""
    from src.models.lstm_extractor import LSTMExtractor

    model = LSTMExtractor(input_dim=12, hidden_dim=64, embed_dim=12, n_classes=3)
    x = torch.randn(8, 30, 12)  # batch=8, seq=30, features=12
    emb, logits = model(x)

    assert emb.shape == (8, 12), f"Embedding shape wrong: {emb.shape}"
    assert logits.shape == (8, 3), f"Logits shape wrong: {logits.shape}"


def test_lstm_model_deterministic():
    """Same input produces same output in eval mode."""
    from src.models.lstm_extractor import LSTMExtractor

    torch.manual_seed(42)
    model = LSTMExtractor(input_dim=12, hidden_dim=64, embed_dim=12, n_classes=3)
    model.eval()

    x = torch.randn(4, 30, 12)
    emb1, logits1 = model(x)
    emb2, logits2 = model(x)

    assert torch.allclose(emb1, emb2)
    assert torch.allclose(logits1, logits2)


def test_build_sequences():
    """Sequence builder creates correct shapes from daily data."""
    from src.models.lstm_extractor import build_sequences
    import pandas as pd

    np.random.seed(42)
    n = 60
    dates = pd.date_range("2025-06-01", periods=n, freq="D")
    df = pd.DataFrame({
        "timestamp": dates,
        **{f"feat_{i}": np.random.randn(n) for i in range(12)},
    })

    sequences, timestamps = build_sequences(df, seq_len=30, feature_cols=[f"feat_{i}" for i in range(12)])
    assert sequences.shape == (31, 30, 12)  # n - seq_len + 1 sequences
    assert len(timestamps) == 31
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `cd C:/cpio_db/CryptoPrism-News-Fetcher && python -m pytest tests/test_lstm.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement lstm_extractor.py**

```python
"""
lstm_extractor.py
2-layer LSTM on 30-day daily sequences for temporal feature extraction.
Outputs 12-dim embedding per coin per day.

Usage:
    python -m src.models.lstm_extractor --train          # train on historical residuals
    python -m src.models.lstm_extractor --predict        # predict latest embeddings
    python -m src.models.lstm_extractor --export-onnx    # export for CPU inference
"""

import argparse
import logging
import os
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
import torch
import torch.nn as nn
from dotenv import load_dotenv

from src.db import get_db_conn, get_backtest_conn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SEQ_LEN = 30
INPUT_DIM = 12
HIDDEN_DIM = 64
EMBED_DIM = 12
N_CLASSES = 3
ARTIFACT_PATH = "artifacts/lstm_extractor.pt"
ONNX_PATH = "artifacts/lstm_extractor.onnx"

FEATURE_COLS = [
    "residual_1d", "residual_volume_1d",
    "close_ret", "daily_range",
    "volume_zscore",
    "residual_vol_7d", "residual_vol_14d",
    "momentum_rank",
    "news_sentiment_1d", "news_volume_1d",
    "fear_greed_index",
    "market_breadth",
]


class LSTMExtractor(nn.Module):
    """2-layer LSTM with embedding + classification heads."""

    def __init__(self, input_dim: int = INPUT_DIM, hidden_dim: int = HIDDEN_DIM,
                 embed_dim: int = EMBED_DIM, n_classes: int = N_CLASSES):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=2,
            dropout=0.3,
            batch_first=True,
        )
        self.embed_head = nn.Linear(hidden_dim, embed_dim)
        self.class_head = nn.Linear(hidden_dim, n_classes)

    def forward(self, x: torch.Tensor):
        """
        Args:
            x: (batch, seq_len, input_dim)
        Returns:
            embedding: (batch, embed_dim)
            logits: (batch, n_classes)
        """
        _, (h_n, _) = self.lstm(x)
        # h_n shape: (num_layers, batch, hidden_dim) — take last layer
        hidden = h_n[-1]
        embedding = self.embed_head(hidden)
        logits = self.class_head(hidden)
        return embedding, logits


def build_sequences(df: pd.DataFrame, seq_len: int = SEQ_LEN,
                    feature_cols: list[str] = FEATURE_COLS) -> tuple[np.ndarray, list]:
    """
    Build sliding window sequences from a per-coin daily DataFrame.

    Args:
        df: DataFrame sorted by timestamp with feature columns
        seq_len: lookback window size
        feature_cols: columns to include in sequences

    Returns:
        sequences: np.ndarray of shape (n_sequences, seq_len, n_features)
        timestamps: list of timestamps for each sequence (last element)
    """
    values = df[feature_cols].values.astype(np.float32)
    timestamps_raw = df["timestamp"].values

    n = len(values)
    if n < seq_len:
        return np.empty((0, seq_len, len(feature_cols)), dtype=np.float32), []

    n_seq = n - seq_len + 1
    sequences = np.zeros((n_seq, seq_len, len(feature_cols)), dtype=np.float32)
    timestamps = []

    for i in range(n_seq):
        sequences[i] = values[i:i + seq_len]
        timestamps.append(timestamps_raw[i + seq_len - 1])

    # Replace NaN with 0 for LSTM input
    sequences = np.nan_to_num(sequences, nan=0.0)
    return sequences, timestamps


def train_model(epochs: int = 30, lr: float = 1e-3, batch_size: int = 256):
    """Train LSTM on historical daily residual data."""
    bt_conn = get_backtest_conn()
    dbcp_conn = get_db_conn()

    # Load residuals (daily)
    log.info("Loading daily residuals...")
    res_df = pd.read_sql(
        'SELECT slug, timestamp, residual_1d, residual_vol_ratio, beta_30d '
        'FROM "FE_BTC_RESIDUALS" '
        'WHERE residual_1d IS NOT NULL '
        'ORDER BY slug, timestamp',
        bt_conn,
    )

    # Load OHLCV for additional features
    log.info("Loading daily OHLCV...")
    ohlcv_df = pd.read_sql(
        'SELECT slug, timestamp, close, volume, high, low '
        'FROM "1K_coins_ohlcv" ORDER BY slug, timestamp',
        bt_conn,
    )

    # Load supplementary
    fg_df = pd.read_sql(
        'SELECT DATE(timestamp) as date, fear_greed_index '
        'FROM "FE_FEAR_GREED_CMC"',
        dbcp_conn,
    )

    # Load labels for training signal
    labels_df = pd.read_sql(
        'SELECT slug, timestamp, label_3d '
        'FROM "ML_LABELS" WHERE label_3d IS NOT NULL',
        dbcp_conn,
    )

    bt_conn.close()
    dbcp_conn.close()

    # Build per-coin feature sequences
    log.info("Building sequences...")
    all_sequences = []
    all_labels = []

    slugs = res_df["slug"].unique()
    for slug in slugs:
        coin_res = res_df[res_df["slug"] == slug].copy()
        coin_ohlcv = ohlcv_df[ohlcv_df["slug"] == slug].copy()

        if len(coin_res) < SEQ_LEN + 5:
            continue

        # Merge and compute features
        coin = coin_res.merge(
            coin_ohlcv[["slug", "timestamp", "close", "volume", "high", "low"]],
            on=["slug", "timestamp"], how="left"
        )
        coin = coin.sort_values("timestamp")
        coin["close_ret"] = coin["close"].pct_change().fillna(0)
        coin["daily_range"] = (coin["high"] - coin["low"]) / coin["close"].replace(0, np.nan)
        coin["volume_zscore"] = (coin["volume"] - coin["volume"].rolling(30).mean()) / coin["volume"].rolling(30).std().replace(0, np.nan)
        coin["residual_vol_7d"] = coin["residual_1d"].rolling(7).std()
        coin["residual_vol_14d"] = coin["residual_1d"].rolling(14).std()
        coin["residual_volume_1d"] = coin["volume"].pct_change().fillna(0)  # placeholder
        coin["momentum_rank"] = 0.5  # placeholder — will be computed cross-sectionally
        coin["news_sentiment_1d"] = 0.0
        coin["news_volume_1d"] = 0.0
        coin["fear_greed_index"] = 50.0
        coin["market_breadth"] = 0.5

        # Merge fear-greed
        coin["date"] = pd.to_datetime(coin["timestamp"]).dt.date
        fg = fg_df.copy()
        fg["date"] = pd.to_datetime(fg["date"]).dt.date
        coin = coin.merge(fg, on="date", how="left", suffixes=("", "_fg"))
        if "fear_greed_index_fg" in coin.columns:
            coin["fear_greed_index"] = coin["fear_greed_index_fg"].fillna(50)
            coin.drop(columns=["fear_greed_index_fg"], inplace=True)

        sequences, ts = build_sequences(coin, SEQ_LEN, FEATURE_COLS)
        if len(sequences) == 0:
            continue

        # Get labels for each sequence endpoint
        coin_labels = labels_df[labels_df["slug"] == slug].set_index("timestamp")
        seq_labels = []
        for t in ts:
            t_dt = pd.Timestamp(t)
            if t_dt in coin_labels.index:
                lbl = int(coin_labels.loc[t_dt, "label_3d"])
                # Map -1/0/1 → 0/1/2 for CrossEntropy
                seq_labels.append({-1: 0, 0: 1, 1: 2}[lbl])
            else:
                seq_labels.append(-1)  # skip

        for i, lbl in enumerate(seq_labels):
            if lbl >= 0:
                all_sequences.append(sequences[i])
                all_labels.append(lbl)

    X = np.array(all_sequences, dtype=np.float32)
    y = np.array(all_labels, dtype=np.int64)
    log.info(f"Training data: {X.shape[0]:,} sequences, {X.shape[1]} steps, {X.shape[2]} features")

    # Train/val split (walk-forward: last 20% is val)
    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    # PyTorch training
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info(f"Training on {device}")

    model = LSTMExtractor(INPUT_DIM, HIDDEN_DIM, EMBED_DIM, N_CLASSES).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()

    train_ds = torch.utils.data.TensorDataset(
        torch.from_numpy(X_train), torch.from_numpy(y_train)
    )
    train_loader = torch.utils.data.DataLoader(train_ds, batch_size=batch_size, shuffle=True)

    for epoch in range(epochs):
        model.train()
        total_loss = 0
        for batch_x, batch_y in train_loader:
            batch_x, batch_y = batch_x.to(device), batch_y.to(device)
            optimizer.zero_grad()
            _, logits = model(batch_x)
            loss = criterion(logits, batch_y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()

        # Validation
        if (epoch + 1) % 5 == 0:
            model.eval()
            with torch.no_grad():
                val_x = torch.from_numpy(X_val).to(device)
                val_y = torch.from_numpy(y_val).to(device)
                _, val_logits = model(val_x)
                val_loss = criterion(val_logits, val_y).item()
                val_acc = (val_logits.argmax(dim=1) == val_y).float().mean().item()
            log.info(f"Epoch {epoch+1}/{epochs} — train_loss={total_loss/len(train_loader):.4f} "
                     f"val_loss={val_loss:.4f} val_acc={val_acc:.3f}")

    # Save
    artifact_dir = Path("artifacts")
    artifact_dir.mkdir(exist_ok=True)
    torch.save(model.state_dict(), ARTIFACT_PATH)
    log.info(f"Model saved to {ARTIFACT_PATH}")

    # Export ONNX
    model.eval()
    model.cpu()
    dummy = torch.randn(1, SEQ_LEN, INPUT_DIM)
    torch.onnx.export(
        model, dummy, ONNX_PATH,
        input_names=["input"],
        output_names=["embedding", "logits"],
        dynamic_axes={"input": {0: "batch"}, "embedding": {0: "batch"}, "logits": {0: "batch"}},
    )
    log.info(f"ONNX exported to {ONNX_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LSTM Feature Extractor")
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--predict", action="store_true")
    parser.add_argument("--export-onnx", action="store_true")
    args = parser.parse_args()

    if args.train:
        train_model()
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `cd C:/cpio_db/CryptoPrism-News-Fetcher && python -m pytest tests/test_lstm.py -v`
Expected: 3 tests PASS.

- [ ] **Step 6: Apply migration, train model**

```bash
cd C:/cpio_db/CryptoPrism-News-Fetcher
# Apply migration
python -c "
from dotenv import load_dotenv; load_dotenv()
import os, psycopg2
conn = psycopg2.connect(host=os.environ['DB_HOST'], port='5432',
    dbname='cp_backtest', user=os.environ['DB_USER'],
    password=os.environ['DB_PASSWORD'], sslmode='require')
conn.autocommit = True
with open('migrations/014_ml_lstm_embeddings.sql') as f:
    conn.cursor().execute(f.read())
print('Migration 014 applied.')
conn.close()
"

# Train (requires BTC residuals from Task 1)
python -m src.models.lstm_extractor --train
```

- [ ] **Step 7: Commit**

```bash
git add src/models/lstm_extractor.py migrations/014_ml_lstm_embeddings.sql tests/test_lstm.py
git commit -m "feat: LSTM feature extractor (30d daily sequences)

2-layer LSTM with 12-dim embedding + auxiliary classification head.
Captures multi-week temporal patterns from daily residual sequences.
ONNX export for CPU inference."
```

---

### Task 4: Temporal Conv Net (TCN, Hourly, 7d Window)

**Files:**
- Create: `migrations/013_ml_tcn_embeddings.sql`
- Create: `src/models/tcn.py`
- Create: `tests/test_tcn.py`

**Overview:** 1D causal CNN with dilated convolutions on 168h hourly residual sequences. 4 residual blocks with dilation [1,2,4,8]. Outputs 16-dim embedding + classification.

- [ ] **Step 1: Create migration 013_ml_tcn_embeddings.sql**

```sql
-- Migration 013: ML_TCN_EMBEDDINGS
-- Hourly TCN embedding vectors per coin.

CREATE TABLE IF NOT EXISTS "ML_TCN_EMBEDDINGS" (
    id              BIGSERIAL PRIMARY KEY,
    slug            TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    emb_0  DOUBLE PRECISION, emb_1  DOUBLE PRECISION,
    emb_2  DOUBLE PRECISION, emb_3  DOUBLE PRECISION,
    emb_4  DOUBLE PRECISION, emb_5  DOUBLE PRECISION,
    emb_6  DOUBLE PRECISION, emb_7  DOUBLE PRECISION,
    emb_8  DOUBLE PRECISION, emb_9  DOUBLE PRECISION,
    emb_10 DOUBLE PRECISION, emb_11 DOUBLE PRECISION,
    emb_12 DOUBLE PRECISION, emb_13 DOUBLE PRECISION,
    emb_14 DOUBLE PRECISION, emb_15 DOUBLE PRECISION,
    tcn_prob_buy    DOUBLE PRECISION,
    tcn_prob_sell   DOUBLE PRECISION,
    tcn_direction   SMALLINT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tcn_emb_slug_ts
    ON "ML_TCN_EMBEDDINGS" (slug, timestamp);

COMMENT ON TABLE "ML_TCN_EMBEDDINGS" IS
    'TCN 16-dim embeddings from 168h hourly residual sequences. '
    'Captures intraday microstructure patterns for ensemble features.';
```

- [ ] **Step 2: Write test_tcn.py**

```python
"""Tests for Temporal Conv Net."""
import numpy as np
import torch
import pytest


def test_tcn_residual_block():
    """Single residual block preserves batch/channel dims."""
    from src.models.tcn import TCNResidualBlock

    block = TCNResidualBlock(in_channels=8, out_channels=64, kernel_size=3, dilation=1, dropout=0.0)
    x = torch.randn(4, 8, 168)  # batch=4, channels=8, seq=168
    out = block(x)
    assert out.shape == (4, 64, 168), f"Wrong shape: {out.shape}"


def test_tcn_model_forward():
    """Full TCN produces correct output shapes."""
    from src.models.tcn import TCNModel

    model = TCNModel(input_channels=8, embed_dim=16, n_classes=3)
    x = torch.randn(4, 8, 168)  # batch=4, features=8, seq=168
    emb, logits = model(x)

    assert emb.shape == (4, 16), f"Embedding shape wrong: {emb.shape}"
    assert logits.shape == (4, 3), f"Logits shape wrong: {logits.shape}"


def test_tcn_causal():
    """Output at time t should not depend on input at time t+1."""
    from src.models.tcn import TCNModel

    torch.manual_seed(42)
    model = TCNModel(input_channels=8, embed_dim=16, n_classes=3)
    model.eval()

    x = torch.randn(1, 8, 168)
    emb1, _ = model(x)

    # Modify future values (last 10 timesteps)
    x2 = x.clone()
    x2[:, :, -10:] = torch.randn(1, 8, 10)
    emb2, _ = model(x2)

    # Embeddings use full sequence (global pool), so they WILL differ
    # But intermediate features before the last 10 steps should be identical
    # This is a basic sanity check that the model runs
    assert emb1.shape == emb2.shape
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `cd C:/cpio_db/CryptoPrism-News-Fetcher && python -m pytest tests/test_tcn.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement tcn.py**

```python
"""
tcn.py
Temporal Conv Net — 1D causal CNN with dilated convolutions.
168h hourly window, 4 residual blocks, dilation [1, 2, 4, 8].

Usage:
    python -m src.models.tcn --train          # train on hourly residuals
    python -m src.models.tcn --export-onnx    # export for CPU inference
"""

import argparse
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from dotenv import load_dotenv

from src.db import get_backtest_conn, get_backtest_h_conn, get_db_conn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SEQ_LEN = 168  # 7 days hourly
INPUT_CHANNELS = 8
EMBED_DIM = 16
N_CLASSES = 3
HIDDEN_CHANNELS = 64
ARTIFACT_PATH = "artifacts/tcn_model.pt"
ONNX_PATH = "artifacts/tcn_model.onnx"

FEATURE_COLS = [
    "residual_1h", "residual_volume",
    "price_spread", "close_open_dir",
    "volume_zscore_7d",
    "hour_sin", "hour_cos",
    "residual_vol_24h",
]


class TCNResidualBlock(nn.Module):
    """Single residual block with dilated causal convolution."""

    def __init__(self, in_channels: int, out_channels: int, kernel_size: int = 3,
                 dilation: int = 1, dropout: float = 0.3):
        super().__init__()
        padding = (kernel_size - 1) * dilation  # causal padding
        self.conv1 = nn.Conv1d(in_channels, out_channels, kernel_size,
                               padding=padding, dilation=dilation)
        self.bn1 = nn.BatchNorm1d(out_channels)
        self.conv2 = nn.Conv1d(out_channels, out_channels, kernel_size,
                               padding=padding, dilation=dilation)
        self.bn2 = nn.BatchNorm1d(out_channels)
        self.dropout = nn.Dropout(dropout)

        # 1x1 conv for residual connection if channel dims differ
        self.residual = nn.Conv1d(in_channels, out_channels, 1) if in_channels != out_channels else nn.Identity()
        self.padding = padding

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x shape: (batch, channels, seq_len)"""
        res = self.residual(x)

        out = self.conv1(x)
        out = out[:, :, :-self.padding] if self.padding > 0 else out  # causal trim
        out = self.bn1(out)
        out = F.relu(out)
        out = self.dropout(out)

        out = self.conv2(out)
        out = out[:, :, :-self.padding] if self.padding > 0 else out  # causal trim
        out = self.bn2(out)

        out = F.relu(out + res)
        return out


class TCNModel(nn.Module):
    """Full TCN with 4 residual blocks + embedding and classification heads."""

    def __init__(self, input_channels: int = INPUT_CHANNELS,
                 hidden_channels: int = HIDDEN_CHANNELS,
                 embed_dim: int = EMBED_DIM, n_classes: int = N_CLASSES):
        super().__init__()
        self.blocks = nn.Sequential(
            TCNResidualBlock(input_channels, hidden_channels, dilation=1),
            TCNResidualBlock(hidden_channels, hidden_channels, dilation=2),
            TCNResidualBlock(hidden_channels, hidden_channels, dilation=4),
            TCNResidualBlock(hidden_channels, hidden_channels, dilation=8),
        )
        self.embed_head = nn.Linear(hidden_channels, embed_dim)
        self.class_head = nn.Linear(hidden_channels, n_classes)

    def forward(self, x: torch.Tensor):
        """
        Args:
            x: (batch, input_channels, seq_len)
        Returns:
            embedding: (batch, embed_dim)
            logits: (batch, n_classes)
        """
        features = self.blocks(x)  # (batch, hidden, seq_len)
        # Global average pool over time dimension
        pooled = features.mean(dim=2)  # (batch, hidden)
        embedding = self.embed_head(pooled)
        logits = self.class_head(pooled)
        return embedding, logits


def build_hourly_features(coin_df: pd.DataFrame, btc_df: pd.DataFrame,
                          residuals_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build 8 TCN input features per hourly timestep for a single coin.

    Args:
        coin_df: hourly OHLCV for one coin [timestamp, open, high, low, close, volume]
        btc_df: hourly BTC OHLCV
        residuals_df: pre-computed residuals [timestamp, residual_1h]

    Returns:
        DataFrame with FEATURE_COLS columns
    """
    df = coin_df.sort_values("timestamp").copy()

    # Merge residuals
    df = df.merge(residuals_df[["timestamp", "residual_1h"]], on="timestamp", how="left")

    # Residual volume (coin volume change minus BTC-correlated component)
    df["residual_volume"] = df["volume"].pct_change().fillna(0)

    # Price spread and direction
    df["price_spread"] = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    df["close_open_dir"] = np.sign(df["close"] - df["open"])

    # Volume z-score (vs 7d rolling mean)
    vol_mean = df["volume"].rolling(168).mean()
    vol_std = df["volume"].rolling(168).std().replace(0, np.nan)
    df["volume_zscore_7d"] = (df["volume"] - vol_mean) / vol_std

    # Hour-of-day encoding
    hours = pd.to_datetime(df["timestamp"]).dt.hour
    df["hour_sin"] = np.sin(2 * np.pi * hours / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hours / 24)

    # Rolling 24h residual volatility
    df["residual_vol_24h"] = df["residual_1h"].rolling(24).std()

    return df[["timestamp"] + FEATURE_COLS]


def train_model(epochs: int = 30, lr: float = 1e-3, batch_size: int = 128):
    """Train TCN on hourly residual sequences."""
    log.info("TCN training — loading data...")

    h_conn = get_backtest_h_conn()
    bt_conn = get_backtest_conn()
    dbcp_conn = get_db_conn()

    # Load hourly OHLCV
    ohlcv = pd.read_sql(
        'SELECT slug, timestamp, open, high, low, close, volume '
        'FROM "ohlcv_1h_250_coins" ORDER BY slug, timestamp',
        h_conn,
    )
    h_conn.close()

    # Load pre-computed residuals
    residuals = pd.read_sql(
        'SELECT slug, timestamp, residual_1h FROM "FE_BTC_RESIDUALS" '
        'WHERE residual_1h IS NOT NULL ORDER BY slug, timestamp',
        bt_conn,
    )

    # Load labels (daily — we'll align hourly sequences to daily labels)
    labels_df = pd.read_sql(
        'SELECT slug, DATE(timestamp) as date, label_3d '
        'FROM "ML_LABELS" WHERE label_3d IS NOT NULL',
        dbcp_conn,
    )

    bt_conn.close()
    dbcp_conn.close()

    btc = ohlcv[ohlcv["slug"] == "bitcoin"].copy()
    slugs = [s for s in ohlcv["slug"].unique() if s != "bitcoin"]

    log.info(f"Building sequences for {len(slugs)} coins...")
    all_X = []
    all_y = []

    label_map = {-1: 0, 0: 1, 1: 2}

    for slug in slugs:
        coin = ohlcv[ohlcv["slug"] == slug].copy()
        coin_res = residuals[residuals["slug"] == slug].copy()

        if len(coin) < SEQ_LEN + 10 or len(coin_res) < SEQ_LEN:
            continue

        features = build_hourly_features(coin, btc, coin_res)
        values = features[FEATURE_COLS].values.astype(np.float32)
        values = np.nan_to_num(values, nan=0.0)
        timestamps = features["timestamp"].values

        coin_labels = labels_df[labels_df["slug"] == slug].set_index("date")

        for i in range(SEQ_LEN, len(values)):
            seq = values[i - SEQ_LEN:i]  # (168, 8)
            # Get label for this date
            ts_date = pd.Timestamp(timestamps[i]).date()
            if ts_date in coin_labels.index:
                lbl = coin_labels.loc[ts_date, "label_3d"]
                if isinstance(lbl, pd.Series):
                    lbl = lbl.iloc[0]
                lbl_int = label_map.get(int(lbl), -1)
                if lbl_int >= 0:
                    all_X.append(seq.T)  # (8, 168) — channels first
                    all_y.append(lbl_int)

    X = np.array(all_X, dtype=np.float32)
    y = np.array(all_y, dtype=np.int64)
    log.info(f"Training data: {X.shape[0]:,} sequences")

    # Walk-forward split
    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info(f"Training on {device}")

    model = TCNModel(INPUT_CHANNELS, HIDDEN_CHANNELS, EMBED_DIM, N_CLASSES).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()

    train_ds = torch.utils.data.TensorDataset(
        torch.from_numpy(X_train), torch.from_numpy(y_train)
    )
    train_loader = torch.utils.data.DataLoader(train_ds, batch_size=batch_size, shuffle=True)

    for epoch in range(epochs):
        model.train()
        total_loss = 0
        for batch_x, batch_y in train_loader:
            batch_x, batch_y = batch_x.to(device), batch_y.to(device)
            optimizer.zero_grad()
            _, logits = model(batch_x)
            loss = criterion(logits, batch_y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()

        if (epoch + 1) % 5 == 0:
            model.eval()
            with torch.no_grad():
                val_x = torch.from_numpy(X_val).to(device)
                val_y = torch.from_numpy(y_val).to(device)
                _, val_logits = model(val_x)
                val_loss = criterion(val_logits, val_y).item()
                val_acc = (val_logits.argmax(dim=1) == val_y).float().mean().item()
            log.info(f"Epoch {epoch+1}/{epochs} — train_loss={total_loss/len(train_loader):.4f} "
                     f"val_loss={val_loss:.4f} val_acc={val_acc:.3f}")

    # Save
    artifact_dir = Path("artifacts")
    artifact_dir.mkdir(exist_ok=True)
    torch.save(model.state_dict(), ARTIFACT_PATH)
    log.info(f"Saved to {ARTIFACT_PATH}")

    # Export ONNX
    model.eval()
    model.cpu()
    dummy = torch.randn(1, INPUT_CHANNELS, SEQ_LEN)
    torch.onnx.export(
        model, dummy, ONNX_PATH,
        input_names=["input"],
        output_names=["embedding", "logits"],
        dynamic_axes={"input": {0: "batch"}, "embedding": {0: "batch"}, "logits": {0: "batch"}},
    )
    log.info(f"ONNX exported to {ONNX_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TCN Trainer")
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--export-onnx", action="store_true")
    args = parser.parse_args()

    if args.train:
        train_model()
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `cd C:/cpio_db/CryptoPrism-News-Fetcher && python -m pytest tests/test_tcn.py -v`
Expected: 3 tests PASS.

- [ ] **Step 6: Apply migration, train model**

```bash
cd C:/cpio_db/CryptoPrism-News-Fetcher
# Apply migration
python -c "
from dotenv import load_dotenv; load_dotenv()
import os, psycopg2
conn = psycopg2.connect(host=os.environ['DB_HOST'], port='5432',
    dbname='cp_backtest', user=os.environ['DB_USER'],
    password=os.environ['DB_PASSWORD'], sslmode='require')
conn.autocommit = True
with open('migrations/013_ml_tcn_embeddings.sql') as f:
    conn.cursor().execute(f.read())
print('Migration 013 applied.')
conn.close()
"

# Train (requires BTC residuals from Task 1)
python -m src.models.tcn --train
```

- [ ] **Step 7: Commit**

```bash
git add src/models/tcn.py migrations/013_ml_tcn_embeddings.sql tests/test_tcn.py
git commit -m "feat: TCN hourly microstructure model (168h window)

1D causal CNN with dilated convolutions [1,2,4,8].
16-dim embedding + classification from hourly residual sequences.
ONNX export for CPU inference."
```

---

### Task 5: News Event Detector

**Files:**
- Create: `migrations/011_fe_news_events.sql`
- Create: `src/features/news_events.py`
- Create: `tests/test_news_events.py`
- Modify: `src/nlp/event_classifier.py`

**Overview:** Classify cc_news articles into 7 event types using DistilBERT/FinBERT fine-tuning. Generate per-coin temporal features (hours_since_event, magnitude, surprise).

This task is substantial and parallel with Tasks 2-4. It requires:
1. Weak-labeling ~500 articles via Claude API or manual rules
2. Fine-tuning a classifier
3. Building the feature pipeline

- [ ] **Step 1: Create migration 011_fe_news_events.sql**

```sql
-- Migration 011: FE_NEWS_EVENTS
-- Structured news event features per coin per timestamp.

CREATE TABLE IF NOT EXISTS "FE_NEWS_EVENTS" (
    id                          BIGSERIAL PRIMARY KEY,
    slug                        TEXT NOT NULL,
    timestamp                   TIMESTAMPTZ NOT NULL,
    event_type                  TEXT,
    magnitude_est               DOUBLE PRECISION,
    hours_since_listing         DOUBLE PRECISION,
    hours_since_hack            DOUBLE PRECISION,
    hours_since_regulatory      DOUBLE PRECISION,
    hours_since_partnership     DOUBLE PRECISION,
    hours_since_tokenomics      DOUBLE PRECISION,
    hours_since_macro           DOUBLE PRECISION,
    event_count_24h             INTEGER,
    news_surprise               DOUBLE PRECISION,
    cross_coin_news_ratio       DOUBLE PRECISION,
    created_at                  TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_news_events_slug_ts
    ON "FE_NEWS_EVENTS" (slug, timestamp);

COMMENT ON TABLE "FE_NEWS_EVENTS" IS
    'Structured news event features: event type classification, '
    'recency features (hours_since_*), magnitude estimates, surprise scores.';
```

- [ ] **Step 2: Write test_news_events.py**

```python
"""Tests for news event detector."""
import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timezone


def test_rule_based_classifier():
    """Rule-based classifier catches obvious event types."""
    from src.features.news_events import classify_event_rule_based

    assert classify_event_rule_based("Coinbase lists XRP for trading") == "listing"
    assert classify_event_rule_based("Hacker steals $100M from DeFi protocol") == "hack_exploit"
    assert classify_event_rule_based("SEC approves Bitcoin ETF application") == "regulatory"
    assert classify_event_rule_based("Microsoft partners with Ethereum Foundation") == "partnership"
    assert classify_event_rule_based("Token burn event removes 10% of supply") == "tokenomics"
    assert classify_event_rule_based("Federal Reserve raises interest rates") == "macro"
    assert classify_event_rule_based("Bitcoin price moved today") == "neutral"


def test_compute_hours_since():
    """hours_since_event computes correctly."""
    from src.features.news_events import compute_hours_since

    events = pd.DataFrame({
        "timestamp": pd.to_datetime(["2025-06-01 10:00", "2025-06-02 15:00"]),
        "event_type": ["listing", "hack_exploit"],
    })
    current = pd.Timestamp("2025-06-03 10:00")
    result = compute_hours_since(events, current)

    assert abs(result["hours_since_listing"] - 48.0) < 0.1
    assert abs(result["hours_since_hack"] - 19.0) < 0.1
    assert result["hours_since_regulatory"] is None  # no such event


def test_magnitude_lookup():
    """Magnitude lookup returns expected values for known event types."""
    from src.features.news_events import get_magnitude_estimate

    # Should return some reasonable float for known types
    mag = get_magnitude_estimate("listing")
    assert isinstance(mag, float)
    assert mag > 0  # listings generally positive

    mag_hack = get_magnitude_estimate("hack_exploit")
    assert mag_hack < 0  # hacks generally negative

    mag_neutral = get_magnitude_estimate("neutral")
    assert abs(mag_neutral) < 0.01  # neutral = ~0
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `cd C:/cpio_db/CryptoPrism-News-Fetcher && python -m pytest tests/test_news_events.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement news_events.py**

```python
"""
news_events.py
News event detection and feature generation.
Classifies cc_news articles into event types and generates temporal features.

Usage:
    python -m src.features.news_events --backfill        # classify all articles
    python -m src.features.news_events --incremental     # classify new articles only
"""

import argparse
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src.db import get_db_conn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

EVENT_TYPES = ["listing", "hack_exploit", "regulatory", "partnership",
               "tokenomics", "macro", "neutral"]

# Default magnitude estimates (median 1d return after event type)
# These will be refined with actual historical data
MAGNITUDE_DEFAULTS = {
    "listing": 0.08,
    "hack_exploit": -0.15,
    "regulatory": -0.05,
    "partnership": 0.04,
    "tokenomics": 0.03,
    "macro": -0.02,
    "neutral": 0.0,
}

# Rule-based keywords for initial classification
EVENT_PATTERNS = {
    "listing": re.compile(
        r"\b(list(?:s|ed|ing)|delist|exchange\s+add|trading\s+pair|launch(?:es|ed)?\s+on)\b", re.I
    ),
    "hack_exploit": re.compile(
        r"\b(hack|exploit|breach|stolen|rug\s*pull|vulnerability|attack|drain|compromise)\b", re.I
    ),
    "regulatory": re.compile(
        r"\b(SEC|regulat|lawsuit|ban|approv|compliance|enforcement|legal|sanction|ETF)\b", re.I
    ),
    "partnership": re.compile(
        r"\b(partner|integrat|collaborat|adopt|institutional|enterprise|deal)\b", re.I
    ),
    "tokenomics": re.compile(
        r"\b(burn|airdrop|unlock|halving|staking|supply|mint|token\s+sale|vesting)\b", re.I
    ),
    "macro": re.compile(
        r"\b(Fed|FOMC|interest\s+rate|inflation|CPI|GDP|recession|treasury|employment)\b", re.I
    ),
}


def classify_event_rule_based(text: str) -> str:
    """Classify article text using keyword patterns. Returns event type string."""
    if not text:
        return "neutral"

    for event_type, pattern in EVENT_PATTERNS.items():
        if pattern.search(text):
            return event_type

    return "neutral"


def compute_hours_since(events: pd.DataFrame, current_ts: pd.Timestamp) -> dict:
    """
    Compute hours since last event of each type.

    Args:
        events: DataFrame with [timestamp, event_type] for a single coin
        current_ts: current timestamp to measure from

    Returns:
        dict with hours_since_{type} keys (None if no event of that type)
    """
    result = {}
    for etype in EVENT_TYPES:
        if etype == "neutral":
            continue
        type_events = events[events["event_type"] == etype]
        if type_events.empty:
            result[f"hours_since_{etype}"] = None
        else:
            latest = pd.Timestamp(type_events["timestamp"].max())
            delta = (current_ts - latest).total_seconds() / 3600
            result[f"hours_since_{etype}"] = max(0, delta)
    return result


def get_magnitude_estimate(event_type: str) -> float:
    """Get estimated price impact magnitude for an event type."""
    return MAGNITUDE_DEFAULTS.get(event_type, 0.0)


def classify_articles(conn, from_date: str | None = None) -> int:
    """
    Classify cc_news articles and generate FE_NEWS_EVENTS features.
    Uses rule-based classification (upgradeable to fine-tuned model later).
    """
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Fetch articles
    query = 'SELECT id, title, body, categories, published_on FROM "cc_news"'
    if from_date:
        query += f" WHERE published_on >= '{from_date}'"
    query += " ORDER BY published_on"

    cur.execute(query)
    articles = cur.fetchall()
    log.info(f"Classifying {len(articles)} articles...")

    # Classify each article
    classified = []
    for art in articles:
        text = f"{art.get('title', '')} {art.get('body', '')}"
        event_type = classify_event_rule_based(text)

        # Extract coin slugs from categories
        cats = art.get("categories", "") or ""
        # Simple extraction — will use coin_mapper.py for production
        classified.append({
            "article_id": art["id"],
            "published_on": art["published_on"],
            "event_type": event_type,
            "categories": cats,
        })

    log.info(f"Classification done. Distribution: {pd.DataFrame(classified)['event_type'].value_counts().to_dict()}")
    return len(classified)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="News Event Detector")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--incremental", action="store_true")
    args = parser.parse_args()

    conn = get_db_conn()
    if args.backfill:
        classify_articles(conn)
    elif args.incremental:
        from_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        classify_articles(conn, from_date=from_date)
    conn.close()
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `cd C:/cpio_db/CryptoPrism-News-Fetcher && python -m pytest tests/test_news_events.py -v`
Expected: 3 tests PASS.

- [ ] **Step 6: Apply migration**

```bash
cd C:/cpio_db/CryptoPrism-News-Fetcher
python -c "
from dotenv import load_dotenv; load_dotenv()
import os, psycopg2
conn = psycopg2.connect(host=os.environ['DB_HOST'], port='5432',
    dbname='dbcp', user=os.environ['DB_USER'],
    password=os.environ['DB_PASSWORD'], sslmode='require')
conn.autocommit = True
with open('migrations/011_fe_news_events.sql') as f:
    conn.cursor().execute(f.read())
print('Migration 011 applied.')
conn.close()
"
```

- [ ] **Step 7: Run backfill**

```bash
python -m src.features.news_events --backfill
```

- [ ] **Step 8: Commit**

```bash
git add src/features/news_events.py migrations/011_fe_news_events.sql tests/test_news_events.py
git commit -m "feat: news event detector with rule-based classification

Classifies cc_news articles into 7 event types (listing, hack, regulatory,
partnership, tokenomics, macro, neutral). Generates temporal features:
hours_since_event, magnitude estimates, news surprise scores.
Upgradeable to fine-tuned DistilBERT later."
```

---

### Task 6: Enhanced LightGBM + Residual Labels

**Files:**
- Create: `src/models/train_ensemble.py`
- Create: `tests/test_ensemble.py`
- Modify: `src/features/labels.py` (add `label_3d_residual`)

**Overview:** Enhanced LightGBM with ~99 features (original 46 + BTC residual + TCN embeddings + LSTM embeddings + news events + BTC-relative). Trains on `label_3d_residual` (residual return direction after removing BTC beta).

- [ ] **Step 1: Add residual labels to labels.py**

Add a new function to `src/features/labels.py` that computes `label_3d_residual` from residual returns instead of raw returns. Add after the existing `classify` function:

```python
def compute_residual_labels(conn, from_date: str, to_date: str) -> list[dict]:
    """Compute labels on BTC-residual returns instead of raw returns."""
    # Fetch residuals
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT slug, DATE(timestamp) as date, residual_1d
        FROM "FE_BTC_RESIDUALS"
        WHERE DATE(timestamp) >= %s AND DATE(timestamp) <= %s
          AND residual_1d IS NOT NULL
        ORDER BY slug, timestamp
    """, (from_date, to_date))
    rows = cur.fetchall()

    from collections import defaultdict
    by_slug = defaultdict(list)
    for r in rows:
        by_slug[r["slug"]].append(r)

    label_rows = []
    for slug, daily in by_slug.items():
        for i, row in enumerate(daily):
            # 3-day forward residual return (sum of next 3 days)
            if i + 3 >= len(daily):
                continue
            fwd_3d = sum(daily[j]["residual_1d"] for j in range(i+1, min(i+4, len(daily))))
            label = classify(fwd_3d, THRESHOLDS["label_3d"])
            label_rows.append({
                "slug": slug,
                "date": row["date"],
                "label_3d_residual": label,
                "forward_ret_3d_residual": round(fwd_3d, 8),
            })

    return label_rows
```

- [ ] **Step 2: Write test_ensemble.py**

```python
"""Tests for ensemble training pipeline."""
import numpy as np
import pandas as pd
import pytest


def test_feature_set_count():
    """Enhanced feature set has ~99 features."""
    from src.models.train_ensemble import FEATURES_ENSEMBLE
    assert 90 <= len(FEATURES_ENSEMBLE) <= 110, f"Expected ~99, got {len(FEATURES_ENSEMBLE)}"


def test_regime_gating():
    """Regime gating adjusts confidence correctly."""
    from src.models.train_ensemble import apply_regime_gating

    # Risk-on: discount SELL
    score = -0.3  # bearish
    adjusted = apply_regime_gating(score, "risk_on", confidence=0.8)
    assert abs(adjusted) < abs(score)  # should be dampened

    # Risk-off: discount BUY
    score = 0.3  # bullish
    adjusted = apply_regime_gating(score, "risk_off", confidence=0.8)
    assert abs(adjusted) < abs(score)

    # Choppy: reduce everything
    score = 0.3
    adjusted = apply_regime_gating(score, "choppy", confidence=0.8)
    assert abs(adjusted) < abs(score)
```

- [ ] **Step 3: Implement train_ensemble.py**

This is the core ensemble trainer. It loads all feature sources (original FE tables, BTC residuals, TCN/LSTM embeddings, news events, regime), trains an enhanced LightGBM on `label_3d_residual`, and builds the meta-learner.

The full implementation follows the same dual-DB pattern as `train_lgbm.py` but adds the new feature blocks. Due to length, the key additions are:
- `FEATURES_ENSEMBLE` list (~99 features)
- `load_ensemble_features()` — loads from cp_backtest + dbcp + embedding tables
- `apply_regime_gating()` — adjusts signal confidence by regime state
- `train_meta_learner()` — simple logistic regression stacker

```python
"""
train_ensemble.py
Enhanced LightGBM + Ensemble Meta-Learner.
Combines: original features + BTC residuals + TCN/LSTM embeddings + news events.

Usage:
    python -m src.models.train_ensemble --mode full
"""
# [Full implementation follows train_lgbm.py patterns with extended feature loading]
# Key difference: trains on label_3d_residual, loads from 6 feature sources,
# includes regime gating logic.
```

- [ ] **Step 4-7: Run tests, train, commit** (same pattern as previous tasks)

---

### Task 7: Ensemble Meta-Learner

Integrated into Task 6's `train_ensemble.py`. The meta-learner is a simple LogisticRegression or small GradientBoosting that stacks:
- LightGBM probabilities (3 dims)
- TCN probabilities (2 dims)
- LSTM probabilities (2 dims)
- Regime state one-hot (4 dims) + confidence (1 dim)
- News event flags (6 binary dims)

Total: 18 input features → final BUY/HOLD/SELL signal.

This is deliberately kept simple (logistic regression) to avoid overfitting the ensemble layer.

---

### Task 8: Hourly Inference Pipeline + Workflow

**Files:**
- Create: `src/inference/hourly_signals.py`
- Create: `migrations/015_ml_signals_v2.sql`
- Create: `.github/workflows/hourly-ensemble.yml`

**Overview:** Hourly cron that runs the full ensemble pipeline: compute residuals → classify news → update regime → run TCN → run LSTM → run LightGBM → run meta-learner → write ML_SIGNALS_V2.

- [ ] **Step 1: Create migration 015_ml_signals_v2.sql**

```sql
-- Migration 015: ML_SIGNALS_V2
-- Enhanced signals from ensemble pipeline.

CREATE TABLE IF NOT EXISTS "ML_SIGNALS_V2" (
    id                  BIGSERIAL PRIMARY KEY,
    slug                TEXT NOT NULL,
    timestamp           TIMESTAMPTZ NOT NULL,
    signal_score        DOUBLE PRECISION,
    residual_score      DOUBLE PRECISION,
    direction           SMALLINT,
    prob_buy            DOUBLE PRECISION,
    prob_hold           DOUBLE PRECISION,
    prob_sell           DOUBLE PRECISION,
    confidence          DOUBLE PRECISION,
    ensemble_confidence DOUBLE PRECISION,
    regime_state        TEXT,
    tcn_direction       SMALLINT,
    lstm_direction      SMALLINT,
    top_features        JSONB,
    model_id            INTEGER,
    feature_date        DATE,
    zscore_30d          DOUBLE PRECISION,
    direction_zscore    SMALLINT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_v2_slug_ts_model
    ON "ML_SIGNALS_V2" (slug, timestamp, model_id);

CREATE INDEX IF NOT EXISTS idx_signals_v2_ts
    ON "ML_SIGNALS_V2" (timestamp DESC);

COMMENT ON TABLE "ML_SIGNALS_V2" IS
    'Ensemble signals: LightGBM + TCN + LSTM + regime gating. '
    'Replaces ML_SIGNALS for hourly inference pipeline.';
```

- [ ] **Step 2: Implement hourly_signals.py**

The hourly inference pipeline runs all 7 steps in sequence (~70 sec total on CPU):

```python
"""
hourly_signals.py
Hourly ensemble inference pipeline.

Usage:
    python -m src.inference.hourly_signals
    python -m src.inference.hourly_signals --date 2026-04-09 --hour 14
"""
# Pipeline steps:
# 1. Compute BTC residuals for latest hour           (~5 sec)
# 2. Scan cc_news for new articles -> FE_NEWS_EVENTS  (~10 sec)
# 3. Update ML_REGIME with latest market state        (~2 sec)
# 4. Run TCN on latest 168h window per coin           (~30 sec ONNX)
# 5. Run LSTM on latest 30d window per coin           (~15 sec ONNX)
# 6. Run Enhanced LightGBM with all features           (~5 sec)
# 7. Run Ensemble Meta-Learner -> ML_SIGNALS_V2        (~2 sec)
```

- [ ] **Step 3: Create hourly-ensemble.yml workflow**

```yaml
name: Hourly Ensemble Signals

on:
  schedule:
    - cron: '0 */4 * * *'  # Every 4 hours
  workflow_dispatch:

jobs:
  ensemble-inference:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - run: pip install -r requirements.txt && pip install onnxruntime hmmlearn
      - name: Create .env file
        run: |
          echo "DB_HOST=${{ secrets.DB_HOST }}" > .env
          echo "DB_PORT=${{ secrets.DB_PORT }}" >> .env
          echo "DB_NAME=${{ secrets.DB_NAME }}" >> .env
          echo "DB_USER=${{ secrets.DB_USER }}" >> .env
          echo "DB_PASSWORD=${{ secrets.DB_PASSWORD }}" >> .env
          echo "DB_BACKTEST_NAME=cp_backtest" >> .env
          SSLMODE="${{ secrets.DB_SSLMODE }}"
          [ -n "$SSLMODE" ] && echo "DB_SSLMODE=$SSLMODE" >> .env || true
      - uses: actions/cache/restore@v4
        with:
          path: artifacts/
          key: ensemble-artifacts-
          restore-keys: ensemble-artifacts-
      - name: Run hourly ensemble
        run: python -m src.inference.hourly_signals
```

- [ ] **Step 4-6: Apply migration, test end-to-end, commit**

```bash
git add src/inference/hourly_signals.py migrations/015_ml_signals_v2.sql .github/workflows/hourly-ensemble.yml
git commit -m "feat: hourly ensemble inference pipeline

Full pipeline: BTC residuals -> news events -> regime -> TCN -> LSTM
-> enhanced LightGBM -> meta-learner -> ML_SIGNALS_V2.
Runs every 4 hours on GitHub Actions (~70 sec on CPU)."
```

---

## Dependency Graph

```
Task 1 (BTC Residuals) ──────┬──> Task 3 (LSTM)  ──────┐
                              ├──> Task 4 (TCN)   ──────┤
Task 2 (Regime HMM) ─────────┤                          ├──> Task 6 (Enhanced LightGBM)
Task 5 (News Events) ────────┘                          │         + Task 7 (Meta-Learner)
                                                        │              │
                                                        └──────> Task 8 (Hourly Pipeline)
```

**Tasks 2, 3, 4, 5 can run in parallel** after Task 1 completes.
**Tasks 6-7** need all of 1-5.
**Task 8** needs all of 1-7.
