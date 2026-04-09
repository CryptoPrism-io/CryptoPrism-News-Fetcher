-- Migration 012: ML_REGIME
-- Market-wide regime classification from HMM.
-- One row per hour — not per coin.

CREATE TABLE IF NOT EXISTS "ML_REGIME" (
    id                      BIGSERIAL PRIMARY KEY,
    timestamp               TIMESTAMPTZ NOT NULL,
    regime_state            TEXT NOT NULL,
    confidence              DOUBLE PRECISION,
    trans_prob_risk_on      DOUBLE PRECISION,
    trans_prob_risk_off     DOUBLE PRECISION,
    trans_prob_choppy       DOUBLE PRECISION,
    trans_prob_breakout     DOUBLE PRECISION,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_regime_ts
    ON "ML_REGIME" (timestamp);

COMMENT ON TABLE "ML_REGIME" IS
    'Market-wide regime state from HMM. One row per hour. '
    'Used to gate ensemble signal confidence.';
