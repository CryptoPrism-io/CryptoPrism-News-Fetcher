-- Migration 019: Widen FE_NEWS_SENTIMENT.news_id INTEGER -> BIGINT
-- Root cause fix for the Daily NLP Pipeline failures that started 2026-07-22.
--
-- cc_news.id is BIGINT. The cryptocurrency.cv ingester (fetch_ccv.py
-- link_to_id) generates signed 64-bit ids from the article URL (e.g.
-- 2792834877969669193, -3501317820277115486). Before cv went to production,
-- only CryptoCompare ids (~53M) were stored, which fit in INTEGER. Once cv
-- articles entered cc_news, the first NLP batch containing one overflowed
-- and psycopg2 aborted the whole 500-row execute_batch with
-- NumericValueOutOfRange: integer out of range.
--
-- This ALTER is idempotent-safe: re-running on a BIGINT column is a no-op
-- (Postgres silently accepts widening to the same type). Apply against dbcp.
--
-- NOTE: FE_NEWS_EVENTS (011) has no news_id column (keyed on slug+timestamp),
-- so it does not need this change.

ALTER TABLE "FE_NEWS_SENTIMENT" ALTER COLUMN news_id TYPE BIGINT;
