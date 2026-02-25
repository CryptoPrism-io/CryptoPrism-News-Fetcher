-- Migration 005: ML_SIGNALS
-- Daily per-coin trading signals from active ML model
-- Written: 2026-02-25

CREATE TABLE IF NOT EXISTS "ML_SIGNALS" (
    id              SERIAL PRIMARY KEY,
    slug            TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,   -- signal generation date (23:59:59 UTC)
    -- Signal outputs
    signal_score    FLOAT NOT NULL,         -- continuous score -1 to +1
    direction       SMALLINT NOT NULL,      -- 1=BUY, 0=HOLD, -1=SELL
    prob_buy        FLOAT,                  -- P(label=1)
    prob_hold       FLOAT,                  -- P(label=0)
    prob_sell       FLOAT,                  -- P(label=-1)
    confidence      FLOAT,                  -- max(prob_buy, prob_hold, prob_sell)
    -- Explainability
    top_features    JSONB,                  -- SHAP top-5: [{"feature":"news_sentiment_3d","shap":0.42}]
    -- Lineage
    model_id        INT NOT NULL,           -- FK reference to ML_MODEL_REGISTRY.model_id
    feature_date    DATE,                   -- date features were taken from
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(slug, timestamp, model_id)
);

CREATE INDEX IF NOT EXISTS idx_ml_signals_slug_ts
    ON "ML_SIGNALS" (slug, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_ml_signals_timestamp
    ON "ML_SIGNALS" (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_ml_signals_direction
    ON "ML_SIGNALS" (direction, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_ml_signals_model
    ON "ML_SIGNALS" (model_id, timestamp DESC);

COMMENT ON TABLE "ML_SIGNALS" IS
    'Daily per-coin trading signals from active ML model. Keyed slug+timestamp+model_id.';
