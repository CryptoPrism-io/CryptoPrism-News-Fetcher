-- Migration 018: FE_CROSS_COIN
-- Cross-sectional features: rank each coin vs all others on a given day.
-- Daily grain, one row per slug per day.
--
-- Target DB: cp_backtest
-- Source: 1K_coins_ohlcv (dbcp, read-only)
-- Written: 2026-04-11

CREATE TABLE IF NOT EXISTS "FE_CROSS_COIN" (
    id                      BIGSERIAL PRIMARY KEY,
    slug                    TEXT NOT NULL,
    timestamp               TIMESTAMPTZ NOT NULL,

    -- Per-coin rank features (0 = worst, 1 = best among all coins that day)
    cc_ret_rank_1d          DOUBLE PRECISION,   -- percentile rank of 1d return
    cc_ret_rank_7d          DOUBLE PRECISION,   -- percentile rank of 7d cumulative return
    cc_vol_rank_1d          DOUBLE PRECISION,   -- percentile rank of volume vs 20d avg
    cc_mktcap_momentum      DOUBLE PRECISION,   -- 7d change in market cap rank (positive = rising)

    -- Market-wide features (same value for all coins on a given day)
    cc_breadth_20d          DOUBLE PRECISION,   -- fraction of coins with close > 20d SMA
    cc_advance_decline      DOUBLE PRECISION,   -- log(advancers / decliners), capped
    cc_dispersion           DOUBLE PRECISION,   -- cross-sectional std of 1d returns
    cc_hhi_volume           DOUBLE PRECISION,   -- Herfindahl index of daily volume (concentration)

    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cross_coin_slug_ts
    ON "FE_CROSS_COIN" (slug, timestamp);

CREATE INDEX IF NOT EXISTS idx_cross_coin_ts
    ON "FE_CROSS_COIN" (timestamp DESC);

COMMENT ON TABLE "FE_CROSS_COIN" IS
    'Cross-sectional features (WS3): momentum rank, volume rank, market breadth, '
    'dispersion, and volume concentration. Daily grain, computed across all coins per day.';
