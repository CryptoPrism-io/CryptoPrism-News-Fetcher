-- Migration 017: FE_RESIDUAL_FEATURES
-- Second-order features derived from FE_BTC_RESIDUALS (hourly residuals).
-- Daily grain: one row per slug per day, computed from trailing hourly windows.
--
-- Target DB: cp_backtest (same as FE_BTC_RESIDUALS)
-- Written: 2026-04-11

CREATE TABLE IF NOT EXISTS "FE_RESIDUAL_FEATURES" (
    id                      BIGSERIAL PRIMARY KEY,
    slug                    TEXT NOT NULL,
    timestamp               TIMESTAMPTZ NOT NULL,

    -- Residual momentum: rolling mean of hourly residuals
    res_momentum_3d         DOUBLE PRECISION,   -- 72h trailing mean
    res_momentum_7d         DOUBLE PRECISION,   -- 168h trailing mean
    res_momentum_14d        DOUBLE PRECISION,   -- 336h trailing mean

    -- Mean-reversion signal: z-score of 24h residual vs 30d rolling distribution
    res_zscore_30d          DOUBLE PRECISION,

    -- Volatility regime: 7d residual vol / 30d residual vol (>1 = expanding)
    res_vol_regime          DOUBLE PRECISION,

    -- Autocorrelation: lag-1 autocorrelation of hourly residuals
    res_autocorr_7d         DOUBLE PRECISION,   -- over trailing 168h
    res_autocorr_14d        DOUBLE PRECISION,   -- over trailing 336h

    -- Conviction-weighted residual: mean(residual_1h * volume_zscore) over 24h
    res_volume_interaction  DOUBLE PRECISION,

    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_res_feat_slug_ts
    ON "FE_RESIDUAL_FEATURES" (slug, timestamp);

CREATE INDEX IF NOT EXISTS idx_res_feat_ts
    ON "FE_RESIDUAL_FEATURES" (timestamp DESC);

COMMENT ON TABLE "FE_RESIDUAL_FEATURES" IS
    'Second-order residual features (WS6): momentum, mean-reversion, vol regime, '
    'autocorrelation, and volume interaction. Daily grain, derived from hourly '
    'FE_BTC_RESIDUALS. Foundation for alpha improvement beyond raw BTC decomposition.';
