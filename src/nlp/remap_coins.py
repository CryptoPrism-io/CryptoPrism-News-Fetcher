"""
remap_coins.py
Re-map coins_mentioned in FE_NEWS_SENTIMENT using expanded coin_mapper.
Does NOT re-run FinBERT — only updates the coin mapping column.

Usage:
    python -m src.nlp.remap_coins
"""

import logging
import sys

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src.db import get_db_conn
from src.nlp.coin_mapper import map_categories_to_slugs

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def remap():
    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Count current state
    cur.execute('SELECT COUNT(DISTINCT u) FROM "FE_NEWS_SENTIMENT", UNNEST(coins_mentioned) AS u')
    before_coins = cur.fetchone()["count"]
    log.info(f"Before: {before_coins} unique coins in FE_NEWS_SENTIMENT")

    # Fetch all articles with their original cc_news data
    cur.execute("""
        SELECT s.id, s.news_id, s.coins_mentioned,
               n.categories, n.title, SUBSTRING(n.body, 1, 500) as body_preview
        FROM "FE_NEWS_SENTIMENT" s
        JOIN "cc_news" n ON n.id = s.news_id
    """)
    articles = cur.fetchall()
    log.info(f"Processing {len(articles):,} articles...")

    updates = []
    expanded_count = 0
    for art in articles:
        old_slugs = art["coins_mentioned"] or []
        new_slugs = map_categories_to_slugs(
            art["categories"] or "",
            include_market_proxy=True,
            title=art["title"] or "",
            body=art["body_preview"] or "",
        )
        if set(new_slugs) != set(old_slugs):
            updates.append({"id": art["id"], "coins_mentioned": new_slugs})
            if len(new_slugs) > len(old_slugs):
                expanded_count += 1

    log.info(f"Articles with changed mapping: {len(updates):,}")
    log.info(f"Articles with MORE coins: {expanded_count:,}")

    if updates:
        update_cur = conn.cursor()
        for batch_start in range(0, len(updates), 1000):
            batch = updates[batch_start:batch_start + 1000]
            for u in batch:
                update_cur.execute(
                    'UPDATE "FE_NEWS_SENTIMENT" SET coins_mentioned = %s WHERE id = %s',
                    (u["coins_mentioned"], u["id"]),
                )
            conn.commit()
            log.info(f"  Updated {min(batch_start + 1000, len(updates)):,}/{len(updates):,}")

    # Count after
    cur.execute('SELECT COUNT(DISTINCT u) FROM "FE_NEWS_SENTIMENT", UNNEST(coins_mentioned) AS u')
    after_coins = cur.fetchone()["count"]
    log.info(f"After: {after_coins} unique coins in FE_NEWS_SENTIMENT (+{after_coins - before_coins})")

    conn.close()


if __name__ == "__main__":
    remap()
