"""
news_signals.py
Aggregates FE_NEWS_SENTIMENT (article-level) → FE_NEWS_SIGNALS (daily per-coin).

Read from: FE_NEWS_SENTIMENT, cc_news (both read-only)
Write to:  FE_NEWS_SIGNALS only

Usage:
    python -m src.features.news_signals --from-date 2025-10-21
    python -m src.features.news_signals  # reprocesses all days with new sentiment scores
"""

import argparse
import logging
import os
from datetime import date, datetime, timedelta, timezone

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

# Tier weights for quality-weighted sentiment
TIER_WEIGHTS = {1: 2.0, 2: 1.0, 3: 0.4}

# Tags that trigger the breaking flag
BREAKING_TAGS = {"breaking news", "breaking"}

# z-score window (days) for volume baseline
ZSCORE_WINDOW_DAYS = 30



def fetch_days_to_process(conn, from_date: str | None, to_date: str | None = None) -> list[date]:
    """
    Return list of dates that have scored articles in FE_NEWS_SENTIMENT
    but are not yet in FE_NEWS_SIGNALS (or are newer than from_date).
    """
    filters = []
    if from_date:
        filters.append(f"published_on >= '{from_date}'")
    if to_date:
        filters.append(f"published_on <= '{to_date}'")
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    query = f"""
        SELECT DISTINCT DATE(published_on) AS day
        FROM "FE_NEWS_SENTIMENT"
        {where}
        ORDER BY day ASC
    """
    with conn.cursor() as cur:
        cur.execute(query)
        return [row[0] for row in cur.fetchall()]


def fetch_daily_sentiment(conn, target_date: date) -> list[dict]:
    """
    Pull all scored articles for a given day, joined with cc_news for tags/categories.
    READ-ONLY on both tables.
    """
    day_start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    query = """
        SELECT
            s.composite_score,
            s.title_score,
            s.confidence,
            s.event_type,
            s.source_tier,
            s.coins_mentioned,
            s.published_on,
            n.tags
        FROM "FE_NEWS_SENTIMENT" s
        JOIN cc_news n ON n.id = s.news_id
        WHERE s.published_on >= %(start)s
          AND s.published_on <  %(end)s
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, {"start": day_start, "end": day_end})
        return cur.fetchall()


def fetch_rolling_sentiment(conn, target_date: date, days: int) -> dict[str, list[float]]:
    """
    Fetch composite scores per coin for a rolling window ending at target_date.
    Returns {slug: [score1, score2, ...]}
    READ-ONLY.
    """
    window_start = datetime.combine(target_date - timedelta(days=days), datetime.min.time()).replace(tzinfo=timezone.utc)
    window_end = datetime.combine(target_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone.utc)

    query = """
        SELECT s.composite_score, s.source_tier, s.coins_mentioned
        FROM "FE_NEWS_SENTIMENT" s
        WHERE s.published_on >= %(start)s
          AND s.published_on <  %(end)s
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, {"start": window_start, "end": window_end})
        rows = cur.fetchall()

    coin_scores: dict[str, list[tuple[float, int]]] = {}
    for row in rows:
        for slug in (row["coins_mentioned"] or []):
            coin_scores.setdefault(slug, []).append(
                (row["composite_score"], row["source_tier"])
            )
    return coin_scores


def fetch_volume_baseline(conn, target_date: date, slug: str) -> tuple[float, float]:
    """
    Return (mean, std) of daily article counts for slug over past ZSCORE_WINDOW_DAYS.
    Used to compute z-score. READ-ONLY.
    """
    window_start = target_date - timedelta(days=ZSCORE_WINDOW_DAYS)

    query = """
        SELECT DATE(published_on) AS day, COUNT(*) AS cnt
        FROM "FE_NEWS_SENTIMENT"
        WHERE published_on >= %(start)s
          AND published_on <  %(end)s
          AND %(slug)s = ANY(coins_mentioned)
        GROUP BY day
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, {
            "start": datetime.combine(window_start, datetime.min.time()).replace(tzinfo=timezone.utc),
            "end":   datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc),
            "slug":  slug,
        })
        counts = [row["cnt"] for row in cur.fetchall()]

    if not counts:
        return 0.0, 1.0

    import statistics
    mean = statistics.mean(counts)
    std = statistics.stdev(counts) if len(counts) > 1 else 1.0
    return mean, max(std, 0.01)  # avoid div-by-zero


def weighted_avg(scores_with_tiers: list[tuple[float, int]]) -> float:
    """Compute tier-weighted average sentiment."""
    if not scores_with_tiers:
        return 0.0
    total_weight = sum(TIER_WEIGHTS.get(t, 1.0) for _, t in scores_with_tiers)
    weighted_sum = sum(s * TIER_WEIGHTS.get(t, 1.0) for s, t in scores_with_tiers)
    return round(weighted_sum / total_weight, 6) if total_weight > 0 else 0.0


def build_signals_for_day(conn, target_date: date) -> list[dict]:
    """
    Build FE_NEWS_SIGNALS rows for all coins mentioned on target_date.
    """
    # Fetch 24h articles
    daily_rows = fetch_daily_sentiment(conn, target_date)
    if not daily_rows:
        log.info(f"  {target_date}: no scored articles, skipping")
        return []

    # Fetch 3d and 7d rolling windows
    rolling_3d = fetch_rolling_sentiment(conn, target_date, days=3)
    rolling_7d = fetch_rolling_sentiment(conn, target_date, days=7)

    # Aggregate 1d per coin
    coin_1d: dict[str, list[tuple[float, int]]] = {}
    coin_tags: dict[str, set[str]] = {}
    coin_events: dict[str, set[str]] = {}
    coin_tier_counts: dict[str, dict[int, int]] = {}
    coin_breaking: dict[str, bool] = {}

    # Timestamp for "past 4 hours" breaking news check
    four_hours_ago = datetime.combine(target_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone.utc) - timedelta(hours=4)

    for row in daily_rows:
        slugs = row["coins_mentioned"] or []
        tags_raw = (row["tags"] or "").lower()
        is_breaking = (
            any(b in tags_raw for b in BREAKING_TAGS)
            and row["published_on"] >= four_hours_ago
        )

        for slug in slugs:
            coin_1d.setdefault(slug, []).append((row["composite_score"], row["source_tier"]))
            coin_events.setdefault(slug, set()).add(row["event_type"])
            coin_tier_counts.setdefault(slug, {1: 0, 2: 0, 3: 0})
            tier = row["source_tier"] or 2
            coin_tier_counts[slug][tier] = coin_tier_counts[slug].get(tier, 0) + 1
            if is_breaking:
                coin_breaking[slug] = True

    # Handle market-proxy (bitcoin) — also collect broad-market articles
    # Articles with no specific coin but tagged CRYPTOCURRENCY/MARKET map to bitcoin via coin_mapper
    # (already handled in sentiment.py with include_market_proxy=True)

    timestamp = datetime.combine(target_date, datetime.max.time().replace(microsecond=0)).replace(tzinfo=timezone.utc)
    # Match FE_ table convention: 23:59:59 UTC
    timestamp = datetime.combine(target_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone.utc) - timedelta(seconds=1)

    signal_rows = []
    all_coins = set(coin_1d.keys()) | set(rolling_3d.keys()) | set(rolling_7d.keys())

    for slug in all_coins:
        scores_1d = coin_1d.get(slug, [])
        scores_3d = rolling_3d.get(slug, [])
        scores_7d = rolling_7d.get(slug, [])

        sentiment_1d = weighted_avg(scores_1d)
        sentiment_3d = weighted_avg(scores_3d)
        sentiment_7d = weighted_avg(scores_7d)
        sentiment_momentum = round(sentiment_1d - sentiment_7d, 6)

        volume_1d = len(scores_1d)
        volume_3d = len(scores_3d)

        # z-score vs 30d baseline
        mean_vol, std_vol = fetch_volume_baseline(conn, target_date, slug)
        zscore = round((volume_1d - mean_vol) / std_vol, 4) if std_vol > 0 else 0.0

        events = coin_events.get(slug, set())
        tiers = coin_tier_counts.get(slug, {1: 0, 2: 0, 3: 0})

        signal_rows.append({
            "slug":                    slug,
            "timestamp":               timestamp,
            "news_sentiment_1d":       sentiment_1d,
            "news_sentiment_3d":       sentiment_3d,
            "news_sentiment_7d":       sentiment_7d,
            "news_sentiment_momentum": sentiment_momentum,
            "news_volume_1d":          volume_1d,
            "news_volume_3d":          volume_3d,
            "news_volume_zscore_1d":   zscore,
            "news_breaking_flag":      1 if coin_breaking.get(slug) else 0,
            "news_regulation_flag":    1 if "REGULATION" in events else 0,
            "news_security_flag":      1 if "HACK_EXPLOIT" in events else 0,
            "news_adoption_flag":      1 if "ADOPTION_PARTNERSHIP" in events else 0,
            "news_source_quality":     weighted_avg(scores_1d),  # already tier-weighted
            "news_tier1_count_1d":     tiers.get(1, 0),
            "news_tier2_count_1d":     tiers.get(2, 0),
            "news_tier3_count_1d":     tiers.get(3, 0),
            "created_at":              datetime.now(timezone.utc),
        })

    return signal_rows


def upsert_signals(conn, rows: list[dict]):
    """Write to FE_NEWS_SIGNALS. ON CONFLICT updates existing rows (re-run safe)."""
    sql = """
        INSERT INTO "FE_NEWS_SIGNALS" (
            slug, timestamp,
            news_sentiment_1d, news_sentiment_3d, news_sentiment_7d, news_sentiment_momentum,
            news_volume_1d, news_volume_3d, news_volume_zscore_1d,
            news_breaking_flag, news_regulation_flag, news_security_flag, news_adoption_flag,
            news_source_quality, news_tier1_count_1d, news_tier2_count_1d, news_tier3_count_1d,
            created_at
        ) VALUES (
            %(slug)s, %(timestamp)s,
            %(news_sentiment_1d)s, %(news_sentiment_3d)s, %(news_sentiment_7d)s, %(news_sentiment_momentum)s,
            %(news_volume_1d)s, %(news_volume_3d)s, %(news_volume_zscore_1d)s,
            %(news_breaking_flag)s, %(news_regulation_flag)s, %(news_security_flag)s, %(news_adoption_flag)s,
            %(news_source_quality)s, %(news_tier1_count_1d)s, %(news_tier2_count_1d)s, %(news_tier3_count_1d)s,
            %(created_at)s
        )
        ON CONFLICT (slug, timestamp) DO UPDATE SET
            news_sentiment_1d       = EXCLUDED.news_sentiment_1d,
            news_sentiment_3d       = EXCLUDED.news_sentiment_3d,
            news_sentiment_7d       = EXCLUDED.news_sentiment_7d,
            news_sentiment_momentum = EXCLUDED.news_sentiment_momentum,
            news_volume_1d          = EXCLUDED.news_volume_1d,
            news_volume_3d          = EXCLUDED.news_volume_3d,
            news_volume_zscore_1d   = EXCLUDED.news_volume_zscore_1d,
            news_breaking_flag      = EXCLUDED.news_breaking_flag,
            news_regulation_flag    = EXCLUDED.news_regulation_flag,
            news_security_flag      = EXCLUDED.news_security_flag,
            news_adoption_flag      = EXCLUDED.news_adoption_flag,
            news_source_quality     = EXCLUDED.news_source_quality,
            news_tier1_count_1d     = EXCLUDED.news_tier1_count_1d,
            news_tier2_count_1d     = EXCLUDED.news_tier2_count_1d,
            news_tier3_count_1d     = EXCLUDED.news_tier3_count_1d,
            created_at              = EXCLUDED.created_at
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
    conn.commit()


def run(from_date: str | None = None, to_date: str | None = None):
    conn = get_db_conn()

    days = fetch_days_to_process(conn, from_date, to_date)
    log.info(f"Days to process: {len(days)} (from {days[0] if days else 'none'} to {days[-1] if days else 'none'})")

    total_rows = 0
    for i, day in enumerate(days):
        log.info(f"[{i+1}/{len(days)}] Building signals for {day}")
        rows = build_signals_for_day(conn, day)
        if rows:
            upsert_signals(conn, rows)
            total_rows += len(rows)
            log.info(f"  → {len(rows)} coin-day rows upserted")

    conn.close()
    log.info(f"Complete. Total rows written to FE_NEWS_SIGNALS: {total_rows}")
    return total_rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Aggregate sentiment → FE_NEWS_SIGNALS")
    parser.add_argument("--from-date", type=str, default=None,
                        help="Start date YYYY-MM-DD (default: all available)")
    parser.add_argument("--to-date", type=str, default=None,
                        help="End date YYYY-MM-DD inclusive (default: today)")
    args = parser.parse_args()
    run(from_date=args.from_date, to_date=args.to_date)
