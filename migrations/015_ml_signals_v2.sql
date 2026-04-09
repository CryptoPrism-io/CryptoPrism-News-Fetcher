-- Migration 015: ML_SIGNALS_V2
-- Enhanced signals from ensemble pipeline.

CREATE TABLE IF NOT EXISTS "ML_SIGNALS_V2" (
    id                  BIGSERIAL PRIMARY KEY,
    slug                TEXT NOT NULL,
    timestamp           TIMESTAMPTZ NOT NULL,
    signal_score        DOUBLE PRECISION,
    residual_score      DOUBLE PRECISION,
    direction           SMALLINT,
    prob_buy            DOUBLE PRECISION,
    prob_hold           DOUBLE PRECISION,
    prob_sell           DOUBLE PRECISION,
    confidence          DOUBLE PRECISION,
    ensemble_confidence DOUBLE PRECISION,
    regime_state        TEXT,
    tcn_direction       SMALLINT,
    lstm_direction      SMALLINT,
    top_features        JSONB,
    model_id            INTEGER,
    feature_date        DATE,
    zscore_30d          DOUBLE PRECISION,
    direction_zscore    SMALLINT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_v2_slug_ts_model
    ON "ML_SIGNALS_V2" (slug, timestamp, model_id);

CREATE INDEX IF NOT EXISTS idx_signals_v2_ts
    ON "ML_SIGNALS_V2" (timestamp DESC);

COMMENT ON TABLE "ML_SIGNALS_V2" IS
    'Ensemble signals: LightGBM + TCN + LSTM + regime gating. '
    'Replaces ML_SIGNALS for hourly inference pipeline.';
