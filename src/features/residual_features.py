"""
residual_features.py
Second-order residual features (WS6) — derived from hourly BTC residuals.

Reads:  FE_BTC_RESIDUALS   (cp_backtest)     — hourly residuals
        ohlcv_1h_250_coins  (cp_backtest_h)   — hourly volume (for volume interaction)

Writes: FE_RESIDUAL_FEATURES (cp_backtest)    — daily grain

Features computed per slug per day (using trailing hourly windows):
  res_momentum_3d        — 72h rolling mean of residual_1h
  res_momentum_7d        — 168h rolling mean
  res_momentum_14d       — 336h rolling mean
  res_zscore_30d         — z-score of 24h mean residual vs 30d distribution
  res_vol_regime         — 7d residual std / 30d residual std
  res_autocorr_7d        — lag-1 autocorrelation over 168h
  res_autocorr_14d       — lag-1 autocorrelation over 336h
  res_volume_interaction — mean(residual_1h * volume_zscore) over trailing 24h

Usage:
    python -m src.features.residual_features --backfill
    python -m src.features.residual_features                   # incremental (latest day)
"""

import argparse
import io
import logging
from datetime import timedelta

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from src.db import get_backtest_conn, get_backtest_h_conn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Window sizes in hours
H3D = 3 * 24     # 72
H7D = 7 * 24     # 168
H14D = 14 * 24   # 336
H30D = 30 * 24   # 720
H24 = 24


def _autocorr_lag1(arr: np.ndarray) -> float:
    """Lag-1 autocorrelation of a 1-D array. Returns NaN if insufficient data."""
    if len(arr) < 10:
        return np.nan
    x = arr[:-1]
    y = arr[1:]
    mx, my = np.mean(x), np.mean(y)
    cov = np.mean((x - mx) * (y - my))
    sx = np.std(x, ddof=0)
    sy = np.std(y, ddof=0)
    if sx == 0 or sy == 0:
        return np.nan
    return cov / (sx * sy)


def compute_daily_features(res_df: pd.DataFrame, vol_df: pd.DataFrame | None) -> pd.DataFrame:
    """
    Compute daily second-order features for a single slug.

    Args:
        res_df: Hourly residuals with columns [timestamp, residual_1h],
                sorted by timestamp ascending.
        vol_df: Hourly volume with columns [timestamp, volume],
                sorted by timestamp ascending. Can be None (volume feature → NaN).

    Returns:
        DataFrame with one row per day: [timestamp, res_momentum_3d, ..., res_volume_interaction]
    """
    if len(res_df) < H7D:
        return pd.DataFrame()

    res_df = res_df.sort_values("timestamp").reset_index(drop=True)
    residuals = res_df["residual_1h"].values
    timestamps = res_df["timestamp"].values

    # Pre-compute volume z-scores if volume data available
    vol_zscores = None
    if vol_df is not None and not vol_df.empty:
        merged = res_df[["timestamp"]].merge(vol_df[["timestamp", "volume"]], on="timestamp", how="left")
        raw_vol = merged["volume"].values.astype(float)
        # Rolling 30d z-score of volume
        vol_zscores = np.full(len(raw_vol), np.nan)
        for i in range(H30D, len(raw_vol)):
            window = raw_vol[i - H30D:i]
            valid = window[~np.isnan(window)]
            if len(valid) > 10:
                mu = np.mean(valid)
                sigma = np.std(valid, ddof=1)
                if sigma > 0 and not np.isnan(raw_vol[i]):
                    vol_zscores[i] = (raw_vol[i] - mu) / sigma

    # Identify unique days (use the last hourly row per day as that day's anchor)
    res_df["_date"] = pd.to_datetime(res_df["timestamp"]).dt.date
    day_anchors = res_df.groupby("_date").tail(1).index.values

    rows = []
    for idx in day_anchors:
        i = idx  # position in the arrays
        ts = timestamps[i]

        # Need at least 7d of trailing data
        if i < H7D:
            continue

        # Trailing windows of residuals
        r3d = residuals[max(0, i - H3D + 1):i + 1]
        r7d = residuals[max(0, i - H7D + 1):i + 1]
        r14d = residuals[max(0, i - H14D + 1):i + 1] if i >= H14D else np.array([])
        r30d = residuals[max(0, i - H30D + 1):i + 1] if i >= H30D else np.array([])
        r24h = residuals[max(0, i - H24 + 1):i + 1]

        # --- Momentum: rolling means ---
        valid_3d = r3d[~np.isnan(r3d)]
        valid_7d = r7d[~np.isnan(r7d)]
        valid_14d = r14d[~np.isnan(r14d)] if len(r14d) > 0 else np.array([])

        mom_3d = float(np.mean(valid_3d)) if len(valid_3d) >= 24 else np.nan
        mom_7d = float(np.mean(valid_7d)) if len(valid_7d) >= 48 else np.nan
        mom_14d = float(np.mean(valid_14d)) if len(valid_14d) >= 96 else np.nan

        # --- Z-score: 24h mean residual vs 30d distribution ---
        valid_24h = r24h[~np.isnan(r24h)]
        valid_30d = r30d[~np.isnan(r30d)] if len(r30d) > 0 else np.array([])
        if len(valid_24h) >= 12 and len(valid_30d) >= 168:
            mu_24h = np.mean(valid_24h)
            mu_30d = np.mean(valid_30d)
            std_30d = np.std(valid_30d, ddof=1)
            zscore = (mu_24h - mu_30d) / std_30d if std_30d > 0 else np.nan
        else:
            zscore = np.nan

        # --- Vol regime: 7d std / 30d std ---
        if len(valid_7d) >= 48 and len(valid_30d) >= 168:
            std_7d = np.std(valid_7d, ddof=1)
            std_30d_val = np.std(valid_30d, ddof=1)
            vol_regime = std_7d / std_30d_val if std_30d_val > 0 else np.nan
        else:
            vol_regime = np.nan

        # --- Autocorrelation ---
        ac_7d = _autocorr_lag1(valid_7d) if len(valid_7d) >= 48 else np.nan
        ac_14d = _autocorr_lag1(valid_14d) if len(valid_14d) >= 96 else np.nan

        # --- Volume interaction: mean(residual * volume_zscore) over 24h ---
        vol_int = np.nan
        if vol_zscores is not None and i >= H24:
            r_24 = residuals[max(0, i - H24 + 1):i + 1]
            vz_24 = vol_zscores[max(0, i - H24 + 1):i + 1]
            mask = ~(np.isnan(r_24) | np.isnan(vz_24))
            if mask.sum() >= 12:
                vol_int = float(np.mean(r_24[mask] * vz_24[mask]))

        rows.append({
            "timestamp": ts,
            "res_momentum_3d": _round_or_none(mom_3d, 8),
            "res_momentum_7d": _round_or_none(mom_7d, 8),
            "res_momentum_14d": _round_or_none(mom_14d, 8),
            "res_zscore_30d": _round_or_none(zscore, 6),
            "res_vol_regime": _round_or_none(vol_regime, 6),
            "res_autocorr_7d": _round_or_none(ac_7d, 6),
            "res_autocorr_14d": _round_or_none(ac_14d, 6),
            "res_volume_interaction": _round_or_none(vol_int, 8),
        })

    return pd.DataFrame(rows)


def _round_or_none(val, decimals: int):
    if val is None or np.isnan(val):
        return None
    return round(float(val), decimals)


def upsert_features(conn, slug: str, df: pd.DataFrame):
    """Upsert daily residual features using COPY + temp table."""
    if df.empty:
        return

    cols = [
        "slug", "timestamp", "res_momentum_3d", "res_momentum_7d", "res_momentum_14d",
        "res_zscore_30d", "res_vol_regime", "res_autocorr_7d", "res_autocorr_14d",
        "res_volume_interaction",
    ]

    df = df.copy()
    df["slug"] = slug

    buf = io.StringIO()
    for _, r in df[cols].iterrows():
        vals = []
        for c in cols:
            v = r[c]
            if v is None or (isinstance(v, float) and np.isnan(v)):
                vals.append("\\N")
            else:
                vals.append(str(v))
        buf.write("\t".join(vals) + "\n")
    buf.seek(0)

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE _res_feat_staging (
                slug TEXT, timestamp TIMESTAMPTZ,
                res_momentum_3d DOUBLE PRECISION,
                res_momentum_7d DOUBLE PRECISION,
                res_momentum_14d DOUBLE PRECISION,
                res_zscore_30d DOUBLE PRECISION,
                res_vol_regime DOUBLE PRECISION,
                res_autocorr_7d DOUBLE PRECISION,
                res_autocorr_14d DOUBLE PRECISION,
                res_volume_interaction DOUBLE PRECISION
            ) ON COMMIT DROP
        """)
        cur.copy_from(buf, "_res_feat_staging", columns=cols, null="\\N")
        cur.execute("""
            INSERT INTO "FE_RESIDUAL_FEATURES" (
                slug, timestamp, res_momentum_3d, res_momentum_7d, res_momentum_14d,
                res_zscore_30d, res_vol_regime, res_autocorr_7d, res_autocorr_14d,
                res_volume_interaction
            )
            SELECT slug, timestamp, res_momentum_3d, res_momentum_7d, res_momentum_14d,
                   res_zscore_30d, res_vol_regime, res_autocorr_7d, res_autocorr_14d,
                   res_volume_interaction
            FROM _res_feat_staging
            ON CONFLICT (slug, timestamp) DO UPDATE SET
                res_momentum_3d        = EXCLUDED.res_momentum_3d,
                res_momentum_7d        = EXCLUDED.res_momentum_7d,
                res_momentum_14d       = EXCLUDED.res_momentum_14d,
                res_zscore_30d         = EXCLUDED.res_zscore_30d,
                res_vol_regime         = EXCLUDED.res_vol_regime,
                res_autocorr_7d        = EXCLUDED.res_autocorr_7d,
                res_autocorr_14d       = EXCLUDED.res_autocorr_14d,
                res_volume_interaction = EXCLUDED.res_volume_interaction
        """)
    conn.commit()


def backfill():
    """Full backfill: read all hourly residuals + volume, compute daily features."""
    bt_conn = get_backtest_conn()
    h_conn = get_backtest_h_conn()

    log.info("Loading hourly residuals from FE_BTC_RESIDUALS...")
    df_res = pd.read_sql(
        'SELECT slug, timestamp, residual_1h FROM "FE_BTC_RESIDUALS" '
        'WHERE residual_1h IS NOT NULL ORDER BY slug, timestamp',
        bt_conn,
    )
    log.info(f"Loaded {len(df_res):,} residual rows, {df_res['slug'].nunique()} coins")

    log.info("Loading hourly volume from ohlcv_1h_250_coins...")
    df_vol = pd.read_sql(
        'SELECT slug, timestamp, volume FROM "ohlcv_1h_250_coins" ORDER BY slug, timestamp',
        h_conn,
    )
    h_conn.close()
    log.info(f"Loaded {len(df_vol):,} volume rows")

    slugs = sorted(df_res["slug"].unique())
    total_rows = 0

    for i, slug in enumerate(slugs):
        res_s = df_res[df_res["slug"] == slug][["timestamp", "residual_1h"]].copy()
        vol_s = df_vol[df_vol["slug"] == slug][["timestamp", "volume"]].copy() if slug in df_vol["slug"].values else None

        feats = compute_daily_features(res_s, vol_s)
        if not feats.empty:
            upsert_features(bt_conn, slug, feats)
            total_rows += len(feats)

        if (i + 1) % 50 == 0:
            log.info(f"  Processed {i + 1}/{len(slugs)} coins, {total_rows:,} rows upserted")

    bt_conn.close()
    log.info(f"Backfill complete: {total_rows:,} rows across {len(slugs)} coins")


def run_incremental():
    """Incremental: recompute last 31 days of residual features for all coins."""
    bt_conn = get_backtest_conn()
    h_conn = get_backtest_h_conn()

    # Find latest timestamp in FE_RESIDUAL_FEATURES
    with bt_conn.cursor() as cur:
        cur.execute('SELECT MAX(timestamp) FROM "FE_RESIDUAL_FEATURES"')
        latest = cur.fetchone()[0]

    # Need 30d lookback for z-score/vol-regime, so load 60d of residuals
    if latest:
        from_ts = (latest - timedelta(days=60)).strftime("%Y-%m-%d 00:00:00+00")
    else:
        log.info("No existing data — running full backfill instead")
        h_conn.close()
        bt_conn.close()
        backfill()
        return

    log.info(f"Incremental: loading residuals from {from_ts}")
    df_res = pd.read_sql(
        'SELECT slug, timestamp, residual_1h FROM "FE_BTC_RESIDUALS" '
        f"WHERE residual_1h IS NOT NULL AND timestamp >= '{from_ts}' "
        'ORDER BY slug, timestamp',
        bt_conn,
    )

    df_vol = pd.read_sql(
        'SELECT slug, timestamp, volume FROM "ohlcv_1h_250_coins" '
        f"WHERE timestamp >= '{from_ts}' ORDER BY slug, timestamp",
        h_conn,
    )
    h_conn.close()

    slugs = sorted(df_res["slug"].unique())
    total_rows = 0

    for slug in slugs:
        res_s = df_res[df_res["slug"] == slug][["timestamp", "residual_1h"]].copy()
        vol_s = df_vol[df_vol["slug"] == slug][["timestamp", "volume"]].copy() if slug in df_vol["slug"].values else None

        feats = compute_daily_features(res_s, vol_s)
        if not feats.empty:
            # Only upsert rows newer than latest - 1d (avoid rewriting old data)
            cutoff = latest - timedelta(days=1)
            feats = feats[feats["timestamp"] > cutoff]
            if not feats.empty:
                upsert_features(bt_conn, slug, feats)
                total_rows += len(feats)

    bt_conn.close()
    log.info(f"Incremental: upserted {total_rows:,} rows across {len(slugs)} coins")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Second-order residual features (WS6)")
    parser.add_argument("--backfill", action="store_true", help="Full backfill from all hourly data")
    args = parser.parse_args()

    if args.backfill:
        backfill()
    else:
        run_incremental()
