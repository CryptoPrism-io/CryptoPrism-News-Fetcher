-- Migration 013: ML_TCN_EMBEDDINGS
-- Hourly TCN embedding vectors per coin.

CREATE TABLE IF NOT EXISTS "ML_TCN_EMBEDDINGS" (
    id              BIGSERIAL PRIMARY KEY,
    slug            TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    emb_0  DOUBLE PRECISION, emb_1  DOUBLE PRECISION,
    emb_2  DOUBLE PRECISION, emb_3  DOUBLE PRECISION,
    emb_4  DOUBLE PRECISION, emb_5  DOUBLE PRECISION,
    emb_6  DOUBLE PRECISION, emb_7  DOUBLE PRECISION,
    emb_8  DOUBLE PRECISION, emb_9  DOUBLE PRECISION,
    emb_10 DOUBLE PRECISION, emb_11 DOUBLE PRECISION,
    emb_12 DOUBLE PRECISION, emb_13 DOUBLE PRECISION,
    emb_14 DOUBLE PRECISION, emb_15 DOUBLE PRECISION,
    tcn_prob_buy    DOUBLE PRECISION,
    tcn_prob_sell   DOUBLE PRECISION,
    tcn_direction   SMALLINT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tcn_emb_slug_ts
    ON "ML_TCN_EMBEDDINGS" (slug, timestamp);

COMMENT ON TABLE "ML_TCN_EMBEDDINGS" IS
    'TCN 16-dim embeddings from 168h hourly residual sequences. '
    'Captures intraday microstructure patterns for ensemble features.';
