-- Migration 010: FE_BTC_RESIDUALS
-- Stores BTC beta decomposition per coin per timestamp.
-- Computed from rolling 30-day OLS: coin_ret = alpha + beta*btc_ret + epsilon

CREATE TABLE IF NOT EXISTS "FE_BTC_RESIDUALS" (
    id              BIGSERIAL PRIMARY KEY,
    slug            TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    beta_30d        DOUBLE PRECISION,
    alpha_30d       DOUBLE PRECISION,
    residual_1h     DOUBLE PRECISION,
    residual_1d     DOUBLE PRECISION,
    residual_vol_ratio DOUBLE PRECISION,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_btc_res_slug_ts
    ON "FE_BTC_RESIDUALS" (slug, timestamp);

CREATE INDEX IF NOT EXISTS idx_btc_res_ts
    ON "FE_BTC_RESIDUALS" (timestamp DESC);

COMMENT ON TABLE "FE_BTC_RESIDUALS" IS
    'BTC beta decomposition: rolling 30d OLS residuals per coin. '
    'Foundation for all alpha models — strips BTC correlation.';
