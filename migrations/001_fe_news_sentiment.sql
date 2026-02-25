-- Migration 001: FE_NEWS_SENTIMENT
-- Article-level FinBERT/CryptoBERT sentiment scores
-- Source: cc_news (read-only reference)
-- Written: 2026-02-25

CREATE TABLE IF NOT EXISTS "FE_NEWS_SENTIMENT" (
    id              SERIAL PRIMARY KEY,
    news_id         INTEGER NOT NULL,           -- references cc_news.id (no FK constraint to avoid dependency)
    published_on    TIMESTAMPTZ NOT NULL,
    title_score     FLOAT,                      -- FinBERT on title: -1.0 (bearish) to +1.0 (bullish)
    body_score      FLOAT,                      -- FinBERT on body: -1.0 to +1.0
    composite_score FLOAT,                      -- 0.4*title + 0.6*body
    confidence      FLOAT,                      -- model confidence 0-1
    event_type      TEXT,                       -- REGULATION|HACK|ADOPTION|MACRO|PARTNERSHIP|OTHER
    source_tier     SMALLINT,                   -- 1=premium, 2=mid, 3=low-quality
    coins_mentioned TEXT[],                     -- mapped slugs extracted from categories
    model_version   TEXT NOT NULL,              -- e.g. 'finbert-v1', 'cryptobert-v1'
    processed_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(news_id, model_version)              -- idempotent: re-run safe
);

CREATE INDEX IF NOT EXISTS idx_fe_news_sentiment_published
    ON "FE_NEWS_SENTIMENT" (published_on DESC);

CREATE INDEX IF NOT EXISTS idx_fe_news_sentiment_coins
    ON "FE_NEWS_SENTIMENT" USING GIN (coins_mentioned);

CREATE INDEX IF NOT EXISTS idx_fe_news_sentiment_event
    ON "FE_NEWS_SENTIMENT" (event_type);

COMMENT ON TABLE "FE_NEWS_SENTIMENT" IS
    'Article-level NLP sentiment scores from FinBERT/CryptoBERT. Read-only ref to cc_news. Part of ML Tier 1 pipeline.';
