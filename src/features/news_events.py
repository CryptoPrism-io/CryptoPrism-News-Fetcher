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

MAGNITUDE_DEFAULTS = {
    "listing": 0.08,
    "hack_exploit": -0.15,
    "regulatory": -0.05,
    "partnership": 0.04,
    "tokenomics": 0.03,
    "macro": -0.02,
    "neutral": 0.0,
}

EVENT_PATTERNS = {
    "listing": re.compile(
        r"\b(list(?:s|ed|ing)|delist\w*|exchange\s+add\w*|trading\s+pair|launch(?:es|ed)?\s+on)\b", re.I
    ),
    "hack_exploit": re.compile(
        r"\b(hack\w*|exploit\w*|breach\w*|steal\w*|stole\w*|stolen|rug\s*pull\w*|vulnerabilit\w*|attack\w*|drain\w*|compromise\w*)\b", re.I
    ),
    "regulatory": re.compile(
        r"\b(SEC|regulat\w*|lawsuit\w*|ban\w*|approv\w*|compliance|enforcement|legal\w*|sanction\w*|ETF)\b", re.I
    ),
    "partnership": re.compile(
        r"\b(partner\w*|integrat\w*|collaborat\w*|adopt\w*|institutional|enterprise|deal)\b", re.I
    ),
    "tokenomics": re.compile(
        r"\b(burn\w*|airdrop\w*|unlock\w*|halving|stak\w*|supply|mint\w*|token\s+sale|vesting)\b", re.I
    ),
    "macro": re.compile(
        r"\b(Fed\b|FOMC|interest\s+rate\w*|inflation\w*|CPI|GDP|recession\w*|treasury|employment)\b", re.I
    ),
}


def classify_event_rule_based(text: str) -> str:
    """Classify article text using keyword patterns."""
    if not text:
        return "neutral"
    for event_type, pattern in EVENT_PATTERNS.items():
        if pattern.search(text):
            return event_type
    return "neutral"


def compute_hours_since(events: pd.DataFrame, current_ts: pd.Timestamp) -> dict:
    """Compute hours since last event of each type for a single coin."""
    result = {}
    for etype in EVENT_TYPES:
        if etype == "neutral":
            continue
        key = f"hours_since_{etype}"
        type_events = events[events["event_type"] == etype]
        if type_events.empty:
            result[key] = None
        else:
            latest = pd.Timestamp(type_events["timestamp"].max())
            delta = (current_ts - latest).total_seconds() / 3600
            result[key] = max(0, delta)
    return result


def get_magnitude_estimate(event_type: str) -> float:
    """Get estimated price impact magnitude for an event type."""
    return MAGNITUDE_DEFAULTS.get(event_type, 0.0)


def classify_and_generate_features(conn, from_date: str | None = None):
    """
    Classify cc_news articles and generate FE_NEWS_EVENTS features.
    """
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    query = 'SELECT id, title, body, categories, published_on FROM "cc_news"'
    if from_date:
        query += f" WHERE published_on >= '{from_date}'"
    query += " ORDER BY published_on"
    cur.execute(query)
    articles = cur.fetchall()
    log.info(f"Classifying {len(articles)} articles...")

    classified = []
    for art in articles:
        text = f"{art.get('title', '')} {art.get('body', '')}"
        event_type = classify_event_rule_based(text)
        classified.append({
            "article_id": art["id"],
            "published_on": art["published_on"],
            "event_type": event_type,
            "categories": art.get("categories", "") or "",
        })

    dist = pd.DataFrame(classified)["event_type"].value_counts().to_dict()
    log.info(f"Classification distribution: {dist}")
    return classified


def upsert_news_events(conn, rows: list[dict]):
    """Upsert into FE_NEWS_EVENTS."""
    sql = """
        INSERT INTO "FE_NEWS_EVENTS" (
            slug, timestamp, event_type, magnitude_est,
            hours_since_listing, hours_since_hack, hours_since_regulatory,
            hours_since_partnership, hours_since_tokenomics, hours_since_macro,
            event_count_24h, news_surprise, cross_coin_news_ratio
        ) VALUES (
            %(slug)s, %(timestamp)s, %(event_type)s, %(magnitude_est)s,
            %(hours_since_listing)s, %(hours_since_hack)s, %(hours_since_regulatory)s,
            %(hours_since_partnership)s, %(hours_since_tokenomics)s, %(hours_since_macro)s,
            %(event_count_24h)s, %(news_surprise)s, %(cross_coin_news_ratio)s
        )
        ON CONFLICT (slug, timestamp) DO UPDATE SET
            event_type          = EXCLUDED.event_type,
            magnitude_est       = EXCLUDED.magnitude_est,
            hours_since_listing = EXCLUDED.hours_since_listing,
            hours_since_hack    = EXCLUDED.hours_since_hack,
            hours_since_regulatory = EXCLUDED.hours_since_regulatory,
            hours_since_partnership = EXCLUDED.hours_since_partnership,
            hours_since_tokenomics = EXCLUDED.hours_since_tokenomics,
            hours_since_macro   = EXCLUDED.hours_since_macro,
            event_count_24h     = EXCLUDED.event_count_24h,
            news_surprise       = EXCLUDED.news_surprise,
            cross_coin_news_ratio = EXCLUDED.cross_coin_news_ratio
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
    conn.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="News Event Detector")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--incremental", action="store_true")
    args = parser.parse_args()

    conn = get_db_conn()
    if args.backfill:
        classify_and_generate_features(conn)
    elif args.incremental:
        from_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        classify_and_generate_features(conn, from_date=from_date)
    conn.close()
