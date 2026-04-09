"""
btc_residuals.py
BTC beta decomposition — rolling 30-day OLS per coin.
Strips BTC correlation so downstream models see only idiosyncratic alpha.

Usage:
    python -m src.features.btc_residuals                      # incremental (latest hour)
    python -m src.features.btc_residuals --backfill            # full backfill from hourly data
"""

import argparse
import logging
import os
from datetime import timedelta

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src.db import get_backtest_conn, get_backtest_h_conn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

WINDOW_HOURS = 30 * 24  # 30 days in hours


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

        if np.std(x) == 0:
            continue

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
    merged = coin_df.merge(btc_df, on="timestamp", suffixes=("_coin", "_btc"))
    merged = merged.sort_values("timestamp").reset_index(drop=True)

    if len(merged) < window_hours + 1:
        result = coin_df[["timestamp"]].copy()
        for col in ["beta_30d", "alpha_30d", "residual_1h", "residual_vol_ratio"]:
            result[col] = np.nan
        return result

    coin_ret = merged["close_coin"].pct_change().fillna(0).values
    btc_ret = merged["close_btc"].pct_change().fillna(0).values

    ols = rolling_ols(coin_ret, btc_ret, window_hours)

    result = pd.DataFrame({
        "timestamp": merged["timestamp"].values,
        "beta_30d": ols["beta"].values,
        "alpha_30d": ols["alpha"].values,
        "residual_1h": ols["residual"].values,
    })

    vol_ratios = np.full(len(result), np.nan)
    for i in range(window_hours, len(result)):
        vol_ratios[i] = compute_residual_vol_ratio(
            ols["residual"].values[:i + 1],
            coin_ret[:i + 1],
            window_hours,
        )
    result["residual_vol_ratio"] = vol_ratios

    return result


def upsert_residuals(conn, rows: list[dict]):
    """Upsert residual rows into FE_BTC_RESIDUALS using COPY + temp table for speed."""
    import io

    df = pd.DataFrame(rows)
    cols = ["slug", "timestamp", "beta_30d", "alpha_30d",
            "residual_1h", "residual_1d", "residual_vol_ratio"]

    with conn.cursor() as cur:
        # Create temp table
        cur.execute("""
            CREATE TEMP TABLE _btc_res_staging (
                slug TEXT, timestamp TIMESTAMPTZ,
                beta_30d DOUBLE PRECISION, alpha_30d DOUBLE PRECISION,
                residual_1h DOUBLE PRECISION, residual_1d DOUBLE PRECISION,
                residual_vol_ratio DOUBLE PRECISION
            ) ON COMMIT DROP
        """)

        # COPY data into temp table via StringIO
        buf = io.StringIO()
        for _, r in df[cols].iterrows():
            vals = []
            for c in cols:
                v = r[c]
                vals.append("\\N" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v))
            buf.write("\t".join(vals) + "\n")
        buf.seek(0)
        cur.copy_from(buf, "_btc_res_staging", columns=cols, null="\\N")

        # Merge into main table
        cur.execute("""
            INSERT INTO "FE_BTC_RESIDUALS" (slug, timestamp, beta_30d, alpha_30d,
                                            residual_1h, residual_1d, residual_vol_ratio)
            SELECT slug, timestamp, beta_30d, alpha_30d,
                   residual_1h, residual_1d, residual_vol_ratio
            FROM _btc_res_staging
            ON CONFLICT (slug, timestamp) DO UPDATE SET
                beta_30d           = EXCLUDED.beta_30d,
                alpha_30d          = EXCLUDED.alpha_30d,
                residual_1h        = EXCLUDED.residual_1h,
                residual_1d        = EXCLUDED.residual_1d,
                residual_vol_ratio = EXCLUDED.residual_vol_ratio
        """)

    conn.commit()


def backfill_hourly():
    """Backfill FE_BTC_RESIDUALS from full hourly OHLCV history."""
    h_conn = get_backtest_h_conn()
    bt_conn = get_backtest_conn()

    log.info("Fetching hourly OHLCV from cp_backtest_h...")
    df = pd.read_sql(
        'SELECT slug, timestamp, close, volume FROM "ohlcv_1h_250_coins" ORDER BY slug, timestamp',
        h_conn,
    )
    h_conn.close()
    log.info(f"Loaded {len(df):,} hourly rows, {df['slug'].nunique()} coins")

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
            if pd.isna(r["beta_30d"]):
                continue
            all_rows.append({
                "slug": slug,
                "timestamp": r["timestamp"],
                "beta_30d": round(float(r["beta_30d"]), 6),
                "alpha_30d": round(float(r["alpha_30d"]), 8),
                "residual_1h": round(float(r["residual_1h"]), 8),
                "residual_1d": None,
                "residual_vol_ratio": round(float(r["residual_vol_ratio"]), 6)
                    if not pd.isna(r["residual_vol_ratio"]) else None,
            })

        if (i + 1) % 50 == 0:
            log.info(f"  Processed {i + 1}/{len(slugs)} coins, {len(all_rows):,} rows")

    log.info(f"Upserting {len(all_rows):,} residual rows in chunks...")
    chunk_size = 50000
    for start in range(0, len(all_rows), chunk_size):
        chunk = all_rows[start:start + chunk_size]
        try:
            upsert_residuals(bt_conn, chunk)
        except Exception:
            # Reconnect on failure
            bt_conn.close()
            bt_conn = get_backtest_conn()
            upsert_residuals(bt_conn, chunk)
        log.info(f"  Upserted {min(start + chunk_size, len(all_rows)):,}/{len(all_rows):,}")
    bt_conn.close()
    log.info("Hourly backfill complete.")


def run_incremental():
    """Compute residuals for latest available hours."""
    h_conn = get_backtest_h_conn()
    bt_conn = get_backtest_conn()

    cur = bt_conn.cursor()
    cur.execute('SELECT MAX(timestamp) FROM "FE_BTC_RESIDUALS"')
    latest = cur.fetchone()[0]

    from_date = (latest - timedelta(days=31)).strftime("%Y-%m-%d") if latest else "2025-02-01"
    log.info(f"Incremental from {from_date}")

    df = pd.read_sql(
        f'SELECT slug, timestamp, close, volume FROM "ohlcv_1h_250_coins" '
        f'WHERE timestamp >= \'{from_date} 00:00:00+00\' ORDER BY slug, timestamp',
        h_conn,
    )
    h_conn.close()

    btc = df[df["slug"] == "bitcoin"][["timestamp", "close"]].copy()
    slugs = [s for s in df["slug"].unique() if s != "bitcoin"]

    all_rows = []
    for slug in slugs:
        coin = df[df["slug"] == slug][["timestamp", "close"]].copy()
        result = compute_for_slug(coin, btc, WINDOW_HOURS)
        if latest:
            result = result[result["timestamp"] > latest]
        for _, r in result.iterrows():
            if pd.isna(r["beta_30d"]):
                continue
            all_rows.append({
                "slug": slug,
                "timestamp": r["timestamp"],
                "beta_30d": round(float(r["beta_30d"]), 6),
                "alpha_30d": round(float(r["alpha_30d"]), 8),
                "residual_1h": round(float(r["residual_1h"]), 8),
                "residual_1d": None,
                "residual_vol_ratio": round(float(r["residual_vol_ratio"]), 6)
                    if not pd.isna(r["residual_vol_ratio"]) else None,
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
    args = parser.parse_args()

    if args.backfill:
        backfill_hourly()
    else:
        run_incremental()
