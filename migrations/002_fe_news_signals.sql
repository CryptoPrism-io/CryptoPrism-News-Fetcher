-- Migration 002: FE_NEWS_SIGNALS
-- Daily per-coin aggregated news signals
-- Follows FE_*_SIGNALS naming convention (same schema pattern as FE_MOMENTUM_SIGNALS etc.)
-- Joins into mv_ml_feature_matrix via slug + timestamp
-- Written: 2026-02-25

CREATE TABLE IF NOT EXISTS "FE_NEWS_SIGNALS" (
    id                      SERIAL PRIMARY KEY,
    slug                    TEXT NOT NULL,
    timestamp               TIMESTAMPTZ NOT NULL,   -- day boundary (23:59:59 UTC, matches FE_ tables)
    -- Sentiment signals
    news_sentiment_1d       FLOAT,                  -- weighted avg composite_score past 24h
    news_sentiment_3d       FLOAT,                  -- 3-day rolling weighted avg
    news_sentiment_7d       FLOAT,                  -- 7-day rolling weighted avg
    news_sentiment_momentum FLOAT,                  -- news_sentiment_1d - news_sentiment_7d
    -- Volume signals
    news_volume_1d          INTEGER,                -- article count past 24h
    news_volume_3d          INTEGER,                -- article count past 3 days
    news_volume_zscore_1d   FLOAT,                  -- z-score vs 30d rolling baseline
    -- Event flags (binary)
    news_breaking_flag      SMALLINT DEFAULT 0,     -- 1 if Breaking News tag in past 4h
    news_regulation_flag    SMALLINT DEFAULT 0,     -- 1 if REGULATION category in past 48h
    news_security_flag      SMALLINT DEFAULT 0,     -- 1 if SECURITY INCIDENTS in past 48h
    news_adoption_flag      SMALLINT DEFAULT 0,     -- 1 if ADOPTION/PARTNERSHIP event in past 48h
    -- Quality-weighted signal
    news_source_quality     FLOAT,                  -- tier-weighted sentiment (tier1 articles 2x weight)
    -- Article counts by tier
    news_tier1_count_1d     INTEGER DEFAULT 0,      -- premium sources past 24h
    news_tier2_count_1d     INTEGER DEFAULT 0,
    news_tier3_count_1d     INTEGER DEFAULT 0,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(slug, timestamp)                         -- same constraint as FE_ tables
);

CREATE INDEX IF NOT EXISTS idx_fe_news_signals_slug_ts
    ON "FE_NEWS_SIGNALS" (slug, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_fe_news_signals_timestamp
    ON "FE_NEWS_SIGNALS" (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_fe_news_signals_regulation
    ON "FE_NEWS_SIGNALS" (timestamp DESC) WHERE news_regulation_flag = 1;

CREATE INDEX IF NOT EXISTS idx_fe_news_signals_security
    ON "FE_NEWS_SIGNALS" (timestamp DESC) WHERE news_security_flag = 1;

COMMENT ON TABLE "FE_NEWS_SIGNALS" IS
    'Daily per-coin aggregated news signals from FE_NEWS_SENTIMENT. Keyed on slug+timestamp to join with all FE_* tables. Part of ML Tier 1 pipeline.';
