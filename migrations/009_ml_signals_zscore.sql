-- Migration 009: Add z-score normalization columns to ML_SIGNALS
-- Rolling 30-day z-score of signal_score per coin, plus z-score-based direction
-- Written: 2026-03-12

ALTER TABLE "ML_SIGNALS"
    ADD COLUMN IF NOT EXISTS zscore_30d       FLOAT,       -- (score - mean_30d) / std_30d
    ADD COLUMN IF NOT EXISTS direction_zscore SMALLINT;    -- 1=BUY (z>1.5), 0=HOLD, -1=SELL (z<-1.5)

CREATE INDEX IF NOT EXISTS idx_ml_signals_direction_zscore
    ON "ML_SIGNALS" (direction_zscore, timestamp DESC);

COMMENT ON COLUMN "ML_SIGNALS".zscore_30d IS
    'Rolling 30-day z-score of signal_score, computed per coin relative to its own history.';
COMMENT ON COLUMN "ML_SIGNALS".direction_zscore IS
    'Z-score-based direction: 1=BUY (z>1.5), 0=HOLD, -1=SELL (z<-1.5). More actionable than raw direction.';
