"""
cross_coin.py
Cross-sectional features (WS3) — rank each coin vs all others per day.

Reads:  1K_coins_ohlcv (dbcp, read-only) — daily close, volume, market_cap
Writes: FE_CROSS_COIN  (cp_backtest)     — daily grain

Features computed per day across all coins:
  Per-coin:
    cc_ret_rank_1d      — percentile rank of 1d return (0=worst, 1=best)
    cc_ret_rank_7d      — percentile rank of 7d cumulative return
    cc_vol_rank_1d      — percentile rank of volume vs 20d average
    cc_mktcap_momentum  — 7d change in market cap rank (positive = gaining ground)

  Market-wide (same for all coins on a day):
    cc_breadth_20d      — fraction of coins with close > 20d SMA
    cc_advance_decline  — log(advancers/decliners), capped ±3
    cc_dispersion       — cross-sectional std of 1d returns
    cc_hhi_volume       — Herfindahl index of daily volume (0=even, 1=concentrated)

Usage:
    python -m src.features.cross_coin --backfill
    python -m src.features.cross_coin --from-date 2025-01-01
    python -m src.features.cross_coin                          # incremental (last 7 days)
"""

import argparse
import io
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from src.db import get_db_conn, get_backtest_conn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Need 30 days lookback for 20d SMA + 7d returns + buffer
LOOKBACK_DAYS = 35


def load_ohlcv(conn, from_date: str, to_date: str) -> pd.DataFrame:
    """Load daily OHLCV from 1K_coins_ohlcv (read-only). Includes lookback buffer."""
    buf_from = (pd.Timestamp(from_date) - pd.Timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    query = """
        SELECT slug, timestamp, close, volume, market_cap
        FROM "1K_coins_ohlcv"
        WHERE timestamp >= %(from_ts)s AND timestamp <= %(to_ts)s
        ORDER BY timestamp, slug
    """
    df = pd.read_sql(query, conn, params={
        "from_ts": f"{buf_from} 00:00:00+00",
        "to_ts": f"{to_date} 23:59:59+00",
    })
    df["date"] = pd.to_datetime(df["timestamp"]).dt.date
    log.info(f"Loaded {len(df):,} OHLCV rows ({buf_from} → {to_date}), {df['slug'].nunique()} coins")
    return df


def compute_cross_coin(df: pd.DataFrame, from_date: str, to_date: str) -> pd.DataFrame:
    """
    Compute all cross-coin features for [from_date, to_date].
    df must include lookback buffer rows before from_date.
    """
    target_from = pd.Timestamp(from_date).date()
    target_to = pd.Timestamp(to_date).date()

    # Pivot to wide format: rows=date, columns=slug
    close_wide = df.pivot_table(index="date", columns="slug", values="close", aggfunc="last")
    volume_wide = df.pivot_table(index="date", columns="slug", values="volume", aggfunc="last")
    mktcap_wide = df.pivot_table(index="date", columns="slug", values="market_cap", aggfunc="last")

    dates = sorted(close_wide.index)
    all_rows = []

    for d in dates:
        if d < target_from or d > target_to:
            continue

        di = dates.index(d)
        if di < 20:  # need 20d lookback for SMA
            continue

        slugs_today = close_wide.loc[d].dropna().index.tolist()
        if len(slugs_today) < 10:
            continue

        # ── 1d returns ──────────────────────────────────────────────────
        d_prev = dates[di - 1] if di > 0 else None
        if d_prev is None:
            continue

        close_t = close_wide.loc[d]
        close_prev = close_wide.loc[d_prev]
        ret_1d = (close_t - close_prev) / close_prev
        ret_1d = ret_1d.dropna()

        # ── 7d cumulative returns ───────────────────────────────────────
        d_7ago = None
        for offset in range(7, 10):  # allow ±2 day tolerance
            idx = di - offset
            if 0 <= idx < len(dates):
                d_7ago = dates[idx]
                break
        ret_7d = pd.Series(dtype=float)
        if d_7ago is not None:
            close_7ago = close_wide.loc[d_7ago]
            ret_7d = (close_t - close_7ago) / close_7ago
            ret_7d = ret_7d.dropna()

        # ── Volume vs 20d average ───────────────────────────────────────
        vol_today = volume_wide.loc[d]
        lookback_dates = dates[max(0, di - 20):di]
        if len(lookback_dates) >= 10:
            vol_20d_avg = volume_wide.loc[lookback_dates].mean()
            vol_ratio = vol_today / vol_20d_avg.replace(0, np.nan)
            vol_ratio = vol_ratio.dropna()
        else:
            vol_ratio = pd.Series(dtype=float)

        # ── Market cap rank and 7d momentum ─────────────────────────────
        mktcap_t = mktcap_wide.loc[d].dropna()
        mktcap_rank_t = mktcap_t.rank(ascending=True, pct=True)

        mktcap_momentum = pd.Series(dtype=float)
        if d_7ago is not None and d_7ago in mktcap_wide.index:
            mktcap_7ago = mktcap_wide.loc[d_7ago].dropna()
            mktcap_rank_7ago = mktcap_7ago.rank(ascending=True, pct=True)
            common = mktcap_rank_t.index.intersection(mktcap_rank_7ago.index)
            mktcap_momentum = mktcap_rank_t.loc[common] - mktcap_rank_7ago.loc[common]

        # ── Percentile ranks ────────────────────────────────────────────
        ret_rank_1d = ret_1d.rank(pct=True) if len(ret_1d) >= 10 else pd.Series(dtype=float)
        ret_rank_7d = ret_7d.rank(pct=True) if len(ret_7d) >= 10 else pd.Series(dtype=float)
        vol_rank_1d = vol_ratio.rank(pct=True) if len(vol_ratio) >= 10 else pd.Series(dtype=float)

        # ── Market-wide: breadth ────────────────────────────────────────
        sma_dates = dates[max(0, di - 19):di + 1]
        if len(sma_dates) >= 15:
            sma_20 = close_wide.loc[sma_dates].mean()
            above_sma = (close_t > sma_20).sum()
            total_valid = close_t.notna().sum()
            breadth = above_sma / total_valid if total_valid > 0 else np.nan
        else:
            breadth = np.nan

        # ── Market-wide: advance/decline ────────────────────────────────
        n_adv = (ret_1d > 0).sum()
        n_dec = (ret_1d < 0).sum()
        if n_dec > 0:
            ad_raw = n_adv / n_dec
            ad_log = np.log(ad_raw) if ad_raw > 0 else -3.0
            ad_log = max(-3.0, min(3.0, ad_log))  # cap
        else:
            ad_log = 3.0 if n_adv > 0 else 0.0

        # ── Market-wide: dispersion ─────────────────────────────────────
        dispersion = float(ret_1d.std(ddof=1)) if len(ret_1d) >= 10 else np.nan

        # ── Market-wide: HHI of volume ──────────────────────────────────
        vol_valid = vol_today.dropna()
        vol_valid = vol_valid[vol_valid > 0]
        if len(vol_valid) >= 10:
            vol_shares = vol_valid / vol_valid.sum()
            hhi = float((vol_shares ** 2).sum())
        else:
            hhi = np.nan

        # ── Assemble rows for all slugs ─────────────────────────────────
        # Use the original timestamp from the data for this date
        ts_candidates = df.loc[df["date"] == d, "timestamp"]
        ts = ts_candidates.iloc[0] if len(ts_candidates) > 0 else pd.Timestamp(d)

        for slug in slugs_today:
            all_rows.append({
                "slug": slug,
                "timestamp": ts,
                "cc_ret_rank_1d": _r(ret_rank_1d.get(slug), 6),
                "cc_ret_rank_7d": _r(ret_rank_7d.get(slug), 6),
                "cc_vol_rank_1d": _r(vol_rank_1d.get(slug), 6),
                "cc_mktcap_momentum": _r(mktcap_momentum.get(slug), 6),
                "cc_breadth_20d": _r(breadth, 6),
                "cc_advance_decline": _r(ad_log, 6),
                "cc_dispersion": _r(dispersion, 8),
                "cc_hhi_volume": _r(hhi, 8),
            })

    log.info(f"Computed {len(all_rows):,} cross-coin rows")
    return pd.DataFrame(all_rows)


def _r(val, decimals: int):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    return round(float(val), decimals)


def upsert_cross_coin(conn, df: pd.DataFrame):
    """Upsert cross-coin features using COPY + temp table."""
    if df.empty:
        return

    cols = [
        "slug", "timestamp",
        "cc_ret_rank_1d", "cc_ret_rank_7d", "cc_vol_rank_1d", "cc_mktcap_momentum",
        "cc_breadth_20d", "cc_advance_decline", "cc_dispersion", "cc_hhi_volume",
    ]

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
            CREATE TEMP TABLE _cross_coin_staging (
                slug TEXT, timestamp TIMESTAMPTZ,
                cc_ret_rank_1d DOUBLE PRECISION,
                cc_ret_rank_7d DOUBLE PRECISION,
                cc_vol_rank_1d DOUBLE PRECISION,
                cc_mktcap_momentum DOUBLE PRECISION,
                cc_breadth_20d DOUBLE PRECISION,
                cc_advance_decline DOUBLE PRECISION,
                cc_dispersion DOUBLE PRECISION,
                cc_hhi_volume DOUBLE PRECISION
            ) ON COMMIT DROP
        """)
        cur.copy_from(buf, "_cross_coin_staging", columns=cols, null="\\N")
        cur.execute("""
            INSERT INTO "FE_CROSS_COIN" (
                slug, timestamp,
                cc_ret_rank_1d, cc_ret_rank_7d, cc_vol_rank_1d, cc_mktcap_momentum,
                cc_breadth_20d, cc_advance_decline, cc_dispersion, cc_hhi_volume
            )
            SELECT slug, timestamp,
                   cc_ret_rank_1d, cc_ret_rank_7d, cc_vol_rank_1d, cc_mktcap_momentum,
                   cc_breadth_20d, cc_advance_decline, cc_dispersion, cc_hhi_volume
            FROM _cross_coin_staging
            ON CONFLICT (slug, timestamp) DO UPDATE SET
                cc_ret_rank_1d     = EXCLUDED.cc_ret_rank_1d,
                cc_ret_rank_7d     = EXCLUDED.cc_ret_rank_7d,
                cc_vol_rank_1d     = EXCLUDED.cc_vol_rank_1d,
                cc_mktcap_momentum = EXCLUDED.cc_mktcap_momentum,
                cc_breadth_20d     = EXCLUDED.cc_breadth_20d,
                cc_advance_decline = EXCLUDED.cc_advance_decline,
                cc_dispersion      = EXCLUDED.cc_dispersion,
                cc_hhi_volume      = EXCLUDED.cc_hhi_volume
        """)
    conn.commit()


def backfill(from_date: str | None = None):
    """Backfill cross-coin features from daily OHLCV."""
    dbcp = get_db_conn()
    bt = get_backtest_conn()

    if not from_date:
        from_date = "2024-01-01"

    to_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    df = load_ohlcv(dbcp, from_date, to_date)
    dbcp.close()

    feats = compute_cross_coin(df, from_date, to_date)

    # Upsert in chunks (can be large: 1000 coins * 500 days = 500K rows)
    chunk_size = 100_000
    for start in range(0, len(feats), chunk_size):
        chunk = feats.iloc[start:start + chunk_size]
        upsert_cross_coin(bt, chunk)
        log.info(f"  Upserted {min(start + chunk_size, len(feats)):,}/{len(feats):,}")

    bt.close()
    log.info(f"Backfill complete: {len(feats):,} rows")


def run_incremental():
    """Recompute last 7 days of cross-coin features."""
    from_date = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
    to_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    dbcp = get_db_conn()
    bt = get_backtest_conn()

    df = load_ohlcv(dbcp, from_date, to_date)
    dbcp.close()

    feats = compute_cross_coin(df, from_date, to_date)
    if not feats.empty:
        upsert_cross_coin(bt, feats)
    bt.close()
    log.info(f"Incremental: {len(feats):,} rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cross-coin features (WS3)")
    parser.add_argument("--backfill", action="store_true", help="Full backfill")
    parser.add_argument("--from-date", type=str, default=None, help="Backfill start date YYYY-MM-DD")
    args = parser.parse_args()

    if args.backfill or args.from_date:
        backfill(from_date=args.from_date)
    else:
        run_incremental()
