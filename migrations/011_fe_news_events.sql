-- Migration 011: FE_NEWS_EVENTS
-- Structured news event features per coin per timestamp.

CREATE TABLE IF NOT EXISTS "FE_NEWS_EVENTS" (
    id                          BIGSERIAL PRIMARY KEY,
    slug                        TEXT NOT NULL,
    timestamp                   TIMESTAMPTZ NOT NULL,
    event_type                  TEXT,
    magnitude_est               DOUBLE PRECISION,
    hours_since_listing         DOUBLE PRECISION,
    hours_since_hack            DOUBLE PRECISION,
    hours_since_regulatory      DOUBLE PRECISION,
    hours_since_partnership     DOUBLE PRECISION,
    hours_since_tokenomics      DOUBLE PRECISION,
    hours_since_macro           DOUBLE PRECISION,
    event_count_24h             INTEGER,
    news_surprise               DOUBLE PRECISION,
    cross_coin_news_ratio       DOUBLE PRECISION,
    created_at                  TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_news_events_slug_ts
    ON "FE_NEWS_EVENTS" (slug, timestamp);

COMMENT ON TABLE "FE_NEWS_EVENTS" IS
    'Structured news event features: event type classification, '
    'recency features (hours_since_*), magnitude estimates, surprise scores.';
