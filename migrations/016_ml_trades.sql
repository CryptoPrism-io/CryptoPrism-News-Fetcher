-- Migration 016: ML_TRADES
-- Tracks spot trades executed from ML signals.

CREATE TABLE IF NOT EXISTS "ML_TRADES" (
    id              BIGSERIAL PRIMARY KEY,
    slug            TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,           -- BUY or SELL
    entry_price     DOUBLE PRECISION,
    exit_price      DOUBLE PRECISION,
    quantity        DOUBLE PRECISION,
    usdt_size       DOUBLE PRECISION,
    signal_score    DOUBLE PRECISION,
    regime_state    TEXT,
    status          TEXT DEFAULT 'OPEN',     -- OPEN, CLOSED, CANCELLED
    entry_time      TIMESTAMPTZ,
    exit_time       TIMESTAMPTZ,
    hold_days       INTEGER DEFAULT 3,
    pnl_usdt       DOUBLE PRECISION,
    pnl_pct        DOUBLE PRECISION,
    model_id        INTEGER,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trades_status ON "ML_TRADES" (status);
CREATE INDEX IF NOT EXISTS idx_trades_slug ON "ML_TRADES" (slug, entry_time DESC);

COMMENT ON TABLE "ML_TRADES" IS
    'Spot trade log from ML ensemble signals. Binance testnet initially.';
