"""
news_events.py
News event detection and feature generation.
Reads classified events from FE_NEWS_SENTIMENT (already coin-mapped),
computes per-coin-per-date temporal features, writes to FE_NEWS_EVENTS.

Usage:
    python -m src.features.news_events --backfill        # all dates
    python -m src.features.news_events --incremental     # last 7 days
"""

import argparse
import logging
import sys
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
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# Event types that map to hours_since_* columns in FE_NEWS_EVENTS.
# FE_NEWS_SENTIMENT uses uppercase; we normalise to these keys.
EVENT_MAP = {
    "ADOPTION_PARTNERSHIP": "listing",
    "HACK_EXPLOIT":         "hack_exploit",
    "REGULATION":           "regulatory",
    "ETF_INSTITUTIONAL":    "partnership",
    "MACRO":                "macro",
    "WHALE_MOVEMENT":       "tokenomics",
    "LIQUIDATION":          "macro",
    "OTHER":                None,
}

HOURS_SINCE_COLS = [
    "hours_since_listing",
    "hours_since_hack_exploit",
    "hours_since_regulatory",
    "hours_since_partnership",
    "hours_since_tokenomics",
    "hours_since_macro",
]

MAGNITUDE_DEFAULTS = {
    "listing":      0.08,
    "hack_exploit": -0.15,
    "regulatory":   -0.05,
    "partnership":  0.04,
    "tokenomics":   0.03,
    "macro":        -0.02,
}


def load_sentiment_events(conn, from_date=None):
    """Load article-level events from FE_NEWS_SENTIMENT with coin mapping."""
    where = ""
    params = ()
    if from_date:
        where = "WHERE published_on >= %s"
        params = (from_date,)
    sql = f"""
        SELECT published_on, event_type, coins_mentioned, composite_score
        FROM "FE_NEWS_SENTIMENT"
        {where}
        ORDER BY published_on
    """
    df = pd.read_sql(sql, conn, params=params)
    log.info(f"Loaded {len(df):,} scored articles")
    return df


def explode_to_coin_events(df):
    """Expand coins_mentioned arrays into one row per (coin, article)."""
    rows = []
    for _, r in df.iterrows():
        slugs = r["coins_mentioned"]
        if not slugs:
            continue
        event_raw = r["event_type"] or "OTHER"
        mapped = EVENT_MAP.get(event_raw)
        ts = pd.Timestamp(r["published_on"])
        score = r["composite_score"] if r["composite_score"] is not None else 0.0
        for slug in slugs:
            rows.append({
                "slug": slug,
                "timestamp": ts,
                "event_type_mapped": mapped,
                "event_type_raw": event_raw,
                "composite_score": score,
            })
    out = pd.DataFrame(rows)
    log.info(f"Exploded to {len(out):,} coin-event rows across {out['slug'].nunique()} coins")
    return out


def compute_temporal_features(coin_events, all_dates):
    """
    For each coin, for each date, compute:
      - hours_since_* (recency of each event type)
      - event_count_24h
      - news_surprise (z-score of daily event count vs 30d mean)
      - cross_coin_news_ratio (fraction computed later)
      - magnitude_est (avg magnitude of today's events)
    """
    results = []
    slugs = coin_events["slug"].unique()
    log.info(f"Computing temporal features for {len(slugs)} coins x {len(all_dates)} dates...")

    # Pre-compute daily total articles across all coins (for cross_coin_news_ratio)
    coin_events["date"] = coin_events["timestamp"].dt.date
    daily_total = coin_events.groupby("date").size()

    for i, slug in enumerate(slugs):
        ce = coin_events[coin_events["slug"] == slug].sort_values("timestamp")
        if ce.empty:
            continue

        ce_dates = ce.groupby("date")

        for d in all_dates:
            # Events up to this date
            mask = ce["timestamp"].dt.date <= d
            history = ce[mask]

            # Today's events
            today_events = ce[ce["date"] == d]
            today_count = len(today_events)

            # Dominant event type today
            if today_count > 0:
                dominant = today_events["event_type_mapped"].dropna().mode()
                event_type = dominant.iloc[0] if len(dominant) > 0 else None
            else:
                event_type = None

            # hours_since_* features
            row = {"slug": slug, "timestamp": datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc)}
            row["event_type"] = event_type

            d_ts = pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=23, minutes=59, seconds=59)
            for etype in ["listing", "hack_exploit", "regulatory", "partnership", "tokenomics", "macro"]:
                col = f"hours_since_{etype}"
                typed_events = history[history["event_type_mapped"] == etype]
                if typed_events.empty:
                    row[col] = None
                else:
                    latest = typed_events["timestamp"].max()
                    if latest.tzinfo is None:
                        latest = latest.tz_localize("UTC")
                    delta_h = (d_ts - latest).total_seconds() / 3600
                    row[col] = max(0, round(delta_h, 2))

            # magnitude_est: average of today's event magnitudes
            if today_count > 0 and event_type:
                row["magnitude_est"] = MAGNITUDE_DEFAULTS.get(event_type, 0.0)
            else:
                row["magnitude_est"] = 0.0

            # event_count_24h
            row["event_count_24h"] = today_count

            # news_surprise: z-score of today's count vs 30d rolling mean
            lookback = [ce[ce["date"] == (d - timedelta(days=j))].shape[0]
                        for j in range(1, 31)]
            mean_30 = np.mean(lookback) if lookback else 0
            std_30 = np.std(lookback) if lookback else 1
            row["news_surprise"] = round((today_count - mean_30) / max(std_30, 0.1), 4)

            # cross_coin_news_ratio
            total_today = daily_total.get(d, 0)
            row["cross_coin_news_ratio"] = round(today_count / max(total_today, 1), 4)

            results.append(row)

        if (i + 1) % 25 == 0:
            log.info(f"  {i+1}/{len(slugs)} coins, {len(results):,} rows")

    log.info(f"Total: {len(results):,} feature rows")
    return results


def upsert_news_events(conn, rows):
    """Upsert into FE_NEWS_EVENTS with correct column names."""
    if not rows:
        log.warning("No rows to upsert")
        return
    sql = """
        INSERT INTO "FE_NEWS_EVENTS" (
            slug, timestamp, event_type, magnitude_est,
            hours_since_listing, hours_since_hack_exploit, hours_since_regulatory,
            hours_since_partnership, hours_since_tokenomics, hours_since_macro,
            event_count_24h, news_surprise, cross_coin_news_ratio
        ) VALUES (
            %(slug)s, %(timestamp)s, %(event_type)s, %(magnitude_est)s,
            %(hours_since_listing)s, %(hours_since_hack_exploit)s, %(hours_since_regulatory)s,
            %(hours_since_partnership)s, %(hours_since_tokenomics)s, %(hours_since_macro)s,
            %(event_count_24h)s, %(news_surprise)s, %(cross_coin_news_ratio)s
        )
        ON CONFLICT (slug, timestamp) DO UPDATE SET
            event_type              = EXCLUDED.event_type,
            magnitude_est           = EXCLUDED.magnitude_est,
            hours_since_listing     = EXCLUDED.hours_since_listing,
            hours_since_hack_exploit = EXCLUDED.hours_since_hack_exploit,
            hours_since_regulatory  = EXCLUDED.hours_since_regulatory,
            hours_since_partnership = EXCLUDED.hours_since_partnership,
            hours_since_tokenomics  = EXCLUDED.hours_since_tokenomics,
            hours_since_macro       = EXCLUDED.hours_since_macro,
            event_count_24h         = EXCLUDED.event_count_24h,
            news_surprise           = EXCLUDED.news_surprise,
            cross_coin_news_ratio   = EXCLUDED.cross_coin_news_ratio
    """
    batch_size = 500
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, batch, page_size=batch_size)
        conn.commit()
    log.info(f"Upserted {len(rows):,} rows to FE_NEWS_EVENTS")


def backfill(conn):
    """Full backfill: classify all articles and generate features."""
    df = load_sentiment_events(conn)
    if df.empty:
        log.error("No scored articles in FE_NEWS_SENTIMENT")
        return

    coin_events = explode_to_coin_events(df)
    if coin_events.empty:
        log.error("No coin-mapped events after explode")
        return

    # Generate features for all dates with news activity
    all_dates = sorted(coin_events["timestamp"].dt.date.unique())
    log.info(f"Date range: {all_dates[0]} to {all_dates[-1]} ({len(all_dates)} dates)")

    rows = compute_temporal_features(coin_events, all_dates)
    upsert_news_events(conn, rows)

    # Report
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*), COUNT(DISTINCT slug) FROM "FE_NEWS_EVENTS"')
    r = cur.fetchone()
    log.info(f"FE_NEWS_EVENTS: {r[0]:,} rows, {r[1]} coins")


def incremental(conn, days=7):
    """Incremental: process only recent articles."""
    from_date = (datetime.now(timezone.utc) - timedelta(days=days + 30)).strftime("%Y-%m-%d")
    df = load_sentiment_events(conn, from_date=from_date)
    if df.empty:
        log.info("No recent articles to process")
        return

    coin_events = explode_to_coin_events(df)
    if coin_events.empty:
        return

    # Only generate features for recent dates
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    all_dates = sorted(d for d in coin_events["timestamp"].dt.date.unique() if d >= cutoff)
    if not all_dates:
        return

    rows = compute_temporal_features(coin_events, all_dates)
    upsert_news_events(conn, rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="News Event Feature Generator")
    parser.add_argument("--backfill", action="store_true", help="Full backfill from all articles")
    parser.add_argument("--incremental", action="store_true", help="Process last 7 days")
    args = parser.parse_args()

    conn = get_db_conn()
    if args.backfill:
        backfill(conn)
    elif args.incremental:
        incremental(conn)
    else:
        log.info("Specify --backfill or --incremental")
    conn.close()
