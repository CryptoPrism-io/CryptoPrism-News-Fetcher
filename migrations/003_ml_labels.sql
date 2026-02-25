-- Migration 003: ML_LABELS
-- Forward return labels derived from 1K_coins_ohlcv (read-only source)
-- These are the training targets for all ML models
-- Written: 2026-02-25

CREATE TABLE IF NOT EXISTS "ML_LABELS" (
    id              SERIAL PRIMARY KEY,
    slug            TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,   -- decision date t (23:59:59 UTC, matches OHLCV)
    close_price     FLOAT,                  -- close[t] for reference
    -- Raw forward returns
    forward_ret_1d  FLOAT,                  -- (close[t+1] - close[t]) / close[t]
    forward_ret_3d  FLOAT,
    forward_ret_7d  FLOAT,
    forward_ret_14d FLOAT,
    -- Classified labels (3-class: 1=BUY, 0=HOLD, -1=SELL)
    label_1d        SMALLINT,               -- threshold: ±3%
    label_3d        SMALLINT,               -- threshold: ±5%
    label_7d        SMALLINT,               -- threshold: ±7%
    label_14d       SMALLINT,               -- threshold: ±10%
    -- Risk features
    volatility_7d   FLOAT,                  -- rolling 7d std of daily returns
    volatility_30d  FLOAT,
    -- Metadata
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(slug, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_ml_labels_slug_ts
    ON "ML_LABELS" (slug, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_ml_labels_timestamp
    ON "ML_LABELS" (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_ml_labels_label3d
    ON "ML_LABELS" (label_3d, timestamp DESC);

COMMENT ON TABLE "ML_LABELS" IS
    'Forward return labels computed from 1K_coins_ohlcv (read-only source). Training targets for all ML models.';
