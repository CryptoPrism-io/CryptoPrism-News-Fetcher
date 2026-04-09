"""
regime.py
Hidden Markov Model for market regime classification.
4 states: risk_on, risk_off, choppy, breakout.

Usage:
    python -m src.models.regime --train          # fit HMM on historical data
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

from src.db import get_db_conn, get_backtest_conn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

REGIME_NAMES = ["risk_on", "risk_off", "choppy", "breakout"]
ARTIFACT_PATH = "artifacts/regime_hmm.pkl"


def build_regime_features(btc_df: pd.DataFrame, fg_df: pd.DataFrame,
                          breadth: np.ndarray) -> pd.DataFrame:
    """
    Build market-wide regime features from BTC OHLCV + fear/greed + breadth.
    """
    df = btc_df.sort_values("timestamp").copy()
    df["ret"] = df["close"].pct_change()

    df["btc_vol_7d"] = df["ret"].rolling(7).std()
    df["btc_vol_30d"] = df["ret"].rolling(30).std()
    df["btc_vol_ratio"] = df["btc_vol_7d"] / df["btc_vol_30d"].replace(0, np.nan)

    df["btc_mom_24h"] = df["close"].pct_change(1)
    df["btc_mom_72h"] = df["close"].pct_change(3)

    fg = fg_df[["timestamp", "fear_greed_index"]].copy()
    fg = fg.rename(columns={"fear_greed_index": "fear_greed"})
    # Normalize timezone for merge
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    fg["timestamp"] = pd.to_datetime(fg["timestamp"], utc=True)
    df = df.merge(fg, on="timestamp", how="left")
    df["fear_greed"] = df["fear_greed"].ffill().fillna(50)

    n = len(df)
    if len(breadth) >= n:
        df["breadth"] = breadth[:n]
    else:
        padded = np.full(n, 0.5)
        padded[-len(breadth):] = breadth
        df["breadth"] = padded

    feature_cols = ["fear_greed", "btc_vol_7d", "btc_vol_30d",
                    "btc_vol_ratio", "btc_mom_24h", "btc_mom_72h", "breadth"]

    return df[["timestamp"] + feature_cols].copy()


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

    valid_mask = ~np.isnan(X).any(axis=1)
    X_valid = X[valid_mask]

    if len(X_valid) < n_states * 10:
        log.error(f"Not enough valid rows for HMM: {len(X_valid)}")
        return None

    model.fit(X_valid)
    return model


def predict_regime(model, X: np.ndarray):
    """Predict regime states and posterior probabilities."""
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
    profiles = {}

    for s in unique:
        mask = states == s
        if mask.sum() == 0:
            continue
        # Features: [fear_greed, btc_vol_7d, btc_vol_30d, vol_ratio, mom_24h, mom_72h, breadth]
        profiles[s] = {
            "vol": np.nanmean(X[mask, 1]),
            "mom": np.nanmean(X[mask, 4]),
            "breadth": np.nanmean(X[mask, 6]),
            "vol_of_vol": np.nanstd(X[mask, 1]),
        }

    if len(profiles) < 2:
        return ["choppy" if s >= 0 else "choppy" for s in states]

    # Assign names by characteristics
    sorted_by_vol = sorted(profiles.keys(), key=lambda s: profiles[s]["vol"])
    name_map = {}

    # Highest vol-of-vol = breakout
    breakout_state = max(profiles.keys(), key=lambda s: profiles[s]["vol_of_vol"])
    name_map[breakout_state] = "breakout"

    # Lowest vol with positive mom = risk_on
    remaining = [s for s in sorted_by_vol if s not in name_map]
    if remaining:
        low_vol = remaining[0]
        name_map[low_vol] = "risk_on" if profiles[low_vol]["mom"] >= 0 else "choppy"

    # Highest vol (not breakout) = risk_off
    remaining = [s for s in sorted_by_vol if s not in name_map]
    if remaining:
        high_vol = remaining[-1]
        name_map[high_vol] = "risk_off" if profiles[high_vol]["mom"] < 0 else "choppy"

    # Fill remaining
    for s in sorted_by_vol:
        if s not in name_map:
            name_map[s] = "choppy"

    return [name_map.get(s, "choppy") if s >= 0 else "choppy" for s in states]


def compute_market_breadth_simple(conn, n_days: int = 365) -> tuple[list, np.ndarray]:
    """Compute % of top 50 coins above 20d MA. Returns (dates, breadth_array)."""
    cur = conn.cursor()
    cur.execute("""
        WITH daily AS (
            SELECT slug, DATE(timestamp) as d, close, market_cap,
                   AVG(close) OVER (PARTITION BY slug ORDER BY timestamp
                                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as ma20
            FROM "1K_coins_ohlcv"
            WHERE timestamp >= CURRENT_DATE - %s
        ),
        top50_per_day AS (
            SELECT d, slug, close, ma20,
                   ROW_NUMBER() OVER (PARTITION BY d ORDER BY market_cap DESC NULLS LAST) as rn
            FROM daily WHERE market_cap IS NOT NULL
        )
        SELECT d, COUNT(*) FILTER (WHERE close > ma20)::float / NULLIF(COUNT(*), 0) as breadth
        FROM top50_per_day WHERE rn <= 50
        GROUP BY d ORDER BY d
    """, (n_days,))
    rows = cur.fetchall()
    dates = [r[0] for r in rows]
    breadth = np.array([r[1] if r[1] is not None else 0.5 for r in rows])
    return dates, breadth


def train_and_save():
    """Train HMM on historical data and save artifact."""
    bt_conn = get_backtest_conn()
    dbcp_conn = get_db_conn()

    btc_df = pd.read_sql(
        'SELECT timestamp, close, volume FROM "1K_coins_ohlcv" '
        "WHERE slug = 'bitcoin' ORDER BY timestamp",
        bt_conn,
    )
    log.info(f"Loaded {len(btc_df)} BTC daily rows")

    fg_df = pd.read_sql(
        'SELECT timestamp, fear_greed_index FROM "FE_FEAR_GREED_CMC" ORDER BY timestamp',
        dbcp_conn,
    )

    log.info("Computing market breadth...")
    dates, breadth = compute_market_breadth_simple(bt_conn, n_days=730)
    bt_conn.close()
    dbcp_conn.close()

    # Align breadth to BTC dates
    breadth_map = dict(zip(dates, breadth))
    btc_dates = btc_df["timestamp"].dt.date.tolist()
    full_breadth = np.array([breadth_map.get(d, 0.5) for d in btc_dates])

    features = build_regime_features(btc_df, fg_df, full_breadth)
    feature_cols = [c for c in features.columns if c != "timestamp"]
    X = features[feature_cols].values

    log.info("Fitting HMM...")
    model = fit_regime_hmm(X, n_states=4)
    if model is None:
        return

    states, probs = predict_regime(model, X)
    labels = label_states(states, X)

    from collections import Counter
    dist = Counter(labels)
    log.info(f"Regime distribution: {dict(dist)}")

    artifact_dir = Path("artifacts")
    artifact_dir.mkdir(exist_ok=True)
    with open(ARTIFACT_PATH, "wb") as f:
        pickle.dump({"model": model, "feature_cols": feature_cols}, f)
    log.info(f"Saved HMM to {ARTIFACT_PATH}")

    # Backfill ML_REGIME
    dbcp_conn = get_db_conn()
    rows = []
    for i, ts in enumerate(features["timestamp"]):
        if states[i] < 0:
            continue
        rows.append({
            "timestamp": ts,
            "regime_state": labels[i],
            "confidence": float(np.max(probs[i])) if not np.isnan(probs[i]).any() else None,
            "trans_prob_risk_on": float(probs[i, 0]) if not np.isnan(probs[i, 0]) else None,
            "trans_prob_risk_off": float(probs[i, 1]) if not np.isnan(probs[i, 1]) else None,
            "trans_prob_choppy": float(probs[i, 2]) if not np.isnan(probs[i, 2]) else None,
            "trans_prob_breakout": float(probs[i, 3]) if not np.isnan(probs[i, 3]) else None,
        })
    upsert_regime(dbcp_conn, rows)
    dbcp_conn.close()
    log.info(f"Backfilled {len(rows)} regime rows to ML_REGIME")


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
    args = parser.parse_args()

    if args.train:
        train_and_save()
