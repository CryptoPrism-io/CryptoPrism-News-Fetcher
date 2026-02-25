"""
sentiment.py
FinBERT-based sentiment scoring for cc_news articles.
Writes to FE_NEWS_SENTIMENT only. Zero writes to any existing table.

Usage:
    python -m src.nlp.sentiment --batch-size 64 --from-date 2025-10-21
    python -m src.nlp.sentiment --batch-size 64  # processes all unscored articles
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

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

MODEL_VERSION = "finbert-v1"
# Body quality threshold — skip stub articles (confirmed from DB: some are 156 chars)
MIN_BODY_LENGTH = 300



def load_model():
    """Load FinBERT model. Downloads on first run (~500MB), cached after."""
    try:
        from transformers import pipeline
    except ImportError:
        log.error("transformers not installed. Run: pip install transformers torch")
        sys.exit(1)

    log.info("Loading FinBERT model (ProsusAI/finbert)...")
    nlp = pipeline(
        "text-classification",
        model="ProsusAI/finbert",
        tokenizer="ProsusAI/finbert",
        device=-1,          # CPU; set to 0 for GPU
        top_k=None,         # return all 3 class scores
        truncation=True,
        max_length=512,
    )
    log.info("Model loaded.")
    return nlp


def finbert_to_score(results: list[dict]) -> tuple[float, float]:
    """
    Convert FinBERT output to a single score in [-1, +1].

    FinBERT returns: [{'label': 'positive', 'score': 0.9}, {'label': 'negative', ...}, ...]
    Score = positive_prob - negative_prob
    Confidence = max(positive_prob, negative_prob, neutral_prob)

    Returns: (score, confidence)
    """
    label_map = {r["label"].lower(): r["score"] for r in results}
    pos = label_map.get("positive", 0.0)
    neg = label_map.get("negative", 0.0)
    score = round(pos - neg, 6)
    confidence = round(max(label_map.values()), 6)
    return score, confidence


def score_texts(nlp, texts: list[str]) -> list[tuple[float, float]]:
    """Score a batch of texts. Returns list of (score, confidence) tuples."""
    # Truncate to 512 tokens worth of chars (~2000 chars) to stay within model limit
    truncated = [t[:2000] if t else "" for t in texts]
    # Replace empty strings with neutral placeholder
    safe = [t if t.strip() else "neutral market news" for t in truncated]
    outputs = nlp(safe)
    return [finbert_to_score(o) for o in outputs]


def fetch_unscored_articles(conn, from_date: str | None, batch_size: int) -> list[dict]:
    """
    Fetch articles from cc_news not yet in FE_NEWS_SENTIMENT.
    READ-ONLY on cc_news.
    """
    query = """
        SELECT
            n.id,
            n.published_on,
            n.title,
            n.body,
            n.body_length,
            n.categories,
            n.tags,
            n.source_name
        FROM cc_news n
        WHERE n.body_length >= %(min_body)s
          AND NOT EXISTS (
              SELECT 1 FROM "FE_NEWS_SENTIMENT" s
              WHERE s.news_id = n.id
                AND s.model_version = %(model_version)s
          )
          {date_filter}
        ORDER BY n.published_on ASC
        LIMIT %(batch_size)s
    """
    date_filter = "AND n.published_on >= %(from_date)s" if from_date else ""
    query = query.format(date_filter=date_filter)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, {
            "min_body": MIN_BODY_LENGTH,
            "model_version": MODEL_VERSION,
            "from_date": from_date,
            "batch_size": batch_size,
        })
        return cur.fetchall()


def insert_sentiment_batch(conn, rows: list[dict]):
    """Write scored rows to FE_NEWS_SENTIMENT. Upsert-safe via ON CONFLICT DO NOTHING."""
    sql = """
        INSERT INTO "FE_NEWS_SENTIMENT" (
            news_id, published_on, title_score, body_score, composite_score,
            confidence, event_type, source_tier, coins_mentioned, model_version, processed_at
        ) VALUES (
            %(news_id)s, %(published_on)s, %(title_score)s, %(body_score)s, %(composite_score)s,
            %(confidence)s, %(event_type)s, %(source_tier)s, %(coins_mentioned)s,
            %(model_version)s, %(processed_at)s
        )
        ON CONFLICT (news_id, model_version) DO NOTHING
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
    conn.commit()


def run(batch_size: int = 64, from_date: str | None = None):
    """Main loop: fetch → score → insert until all articles processed."""
    from src.nlp.coin_mapper import map_categories_to_slugs, get_source_tier
    from src.nlp.event_classifier import classify_event

    conn = get_db_conn()
    nlp = load_model()

    total_processed = 0
    now = datetime.now(timezone.utc)

    while True:
        articles = fetch_unscored_articles(conn, from_date, batch_size)
        if not articles:
            log.info(f"Done. Total articles scored: {total_processed}")
            break

        log.info(f"Scoring batch of {len(articles)} articles (total so far: {total_processed})")

        # Score titles
        titles = [a["title"] or "" for a in articles]
        title_scores = score_texts(nlp, titles)

        # Score bodies
        bodies = [a["body"] or "" for a in articles]
        body_scores = score_texts(nlp, bodies)

        rows = []
        for article, (t_score, _), (b_score, b_conf) in zip(articles, title_scores, body_scores):
            composite = round(0.4 * t_score + 0.6 * b_score, 6)
            slugs = map_categories_to_slugs(article["categories"], include_market_proxy=True)
            tier = get_source_tier(article["source_name"] or "")
            event = classify_event(article["categories"] or "", article["title"] or "")

            rows.append({
                "news_id":        article["id"],
                "published_on":   article["published_on"],
                "title_score":    t_score,
                "body_score":     b_score,
                "composite_score": composite,
                "confidence":     b_conf,
                "event_type":     event,
                "source_tier":    tier,
                "coins_mentioned": slugs,
                "model_version":  MODEL_VERSION,
                "processed_at":   now,
            })

        insert_sentiment_batch(conn, rows)
        total_processed += len(rows)

    conn.close()
    return total_processed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Score cc_news articles with FinBERT")
    parser.add_argument("--batch-size", type=int, default=64,
                        help="Articles per batch (default: 64)")
    parser.add_argument("--from-date", type=str, default=None,
                        help="Only score articles on/after this date (YYYY-MM-DD)")
    args = parser.parse_args()
    run(batch_size=args.batch_size, from_date=args.from_date)
