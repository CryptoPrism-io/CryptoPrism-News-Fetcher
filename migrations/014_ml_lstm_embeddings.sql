-- Migration 014: ML_LSTM_EMBEDDINGS
-- Daily LSTM embedding vectors per coin.

CREATE TABLE IF NOT EXISTS "ML_LSTM_EMBEDDINGS" (
    id              BIGSERIAL PRIMARY KEY,
    slug            TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    lemb_0  DOUBLE PRECISION, lemb_1  DOUBLE PRECISION,
    lemb_2  DOUBLE PRECISION, lemb_3  DOUBLE PRECISION,
    lemb_4  DOUBLE PRECISION, lemb_5  DOUBLE PRECISION,
    lemb_6  DOUBLE PRECISION, lemb_7  DOUBLE PRECISION,
    lemb_8  DOUBLE PRECISION, lemb_9  DOUBLE PRECISION,
    lemb_10 DOUBLE PRECISION, lemb_11 DOUBLE PRECISION,
    lstm_prob_buy   DOUBLE PRECISION,
    lstm_prob_sell  DOUBLE PRECISION,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_lstm_emb_slug_ts
    ON "ML_LSTM_EMBEDDINGS" (slug, timestamp);

COMMENT ON TABLE "ML_LSTM_EMBEDDINGS" IS
    'LSTM 12-dim embeddings from 30-day daily residual sequences. '
    'Captures multi-week temporal patterns for ensemble features.';
