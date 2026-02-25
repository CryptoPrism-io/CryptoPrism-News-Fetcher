-- Migration 006: ML_BACKTEST_RESULTS
-- Backtest performance metrics per model per window
-- Written: 2026-02-25

CREATE TABLE IF NOT EXISTS "ML_BACKTEST_RESULTS" (
    id              SERIAL PRIMARY KEY,
    model_id        INT NOT NULL,           -- FK reference to ML_MODEL_REGISTRY.model_id
    backtest_from   DATE NOT NULL,
    backtest_to     DATE NOT NULL,
    universe        TEXT NOT NULL,          -- 'top20' | 'top100' | 'all_1387'
    -- Information Coefficients
    ic_1d           FLOAT,                  -- Spearman rank IC vs 1d forward return
    ic_3d           FLOAT,
    ic_7d           FLOAT,
    ic_3d_mean      FLOAT,                  -- rolling mean IC (stability)
    ic_3d_std       FLOAT,                  -- rolling std IC (consistency)
    icir            FLOAT,                  -- IC / std(IC) — information ratio
    -- Classification metrics
    accuracy        FLOAT,
    precision_buy   FLOAT,
    recall_buy      FLOAT,
    f1_buy          FLOAT,
    -- Portfolio simulation
    sharpe          FLOAT,
    max_drawdown    FLOAT,
    total_return    FLOAT,
    win_rate        FLOAT,
    total_trades    INT,
    avg_holding_days FLOAT,
    -- Metadata
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ml_backtest_model
    ON "ML_BACKTEST_RESULTS" (model_id, backtest_from DESC);

COMMENT ON TABLE "ML_BACKTEST_RESULTS" IS
    'Backtest performance metrics per model. IC, Sharpe, drawdown per universe and date window.';
