-- Migration 004: ML_MODEL_REGISTRY
-- Tracks trained model metadata, performance, and artifact paths
-- Written: 2026-02-25

CREATE TABLE IF NOT EXISTS "ML_MODEL_REGISTRY" (
    model_id        SERIAL PRIMARY KEY,
    model_name      TEXT NOT NULL UNIQUE,   -- e.g. 'lgbm_price_only_v1'
    model_type      TEXT NOT NULL,          -- 'lightgbm' | 'xgboost' | 'tft' | 'itransformer'
    target          TEXT NOT NULL,          -- 'label_3d' | 'forward_ret_3d'
    features_used   JSONB NOT NULL,         -- list of feature column names
    hyperparameters JSONB,                  -- model hyperparams for reproducibility
    train_from      DATE NOT NULL,
    train_to        DATE NOT NULL,
    val_from        DATE,
    val_to          DATE,
    universe        TEXT,                   -- 'top20' | 'top100' | 'all_1387'
    -- Validation metrics
    val_ic_1d       FLOAT,                  -- information coefficient vs 1d forward return
    val_ic_3d       FLOAT,
    val_ic_7d       FLOAT,
    val_accuracy    FLOAT,                  -- classification accuracy
    val_sharpe      FLOAT,                  -- simulated portfolio sharpe on val set
    val_win_rate    FLOAT,
    -- Deployment
    artifact_path   TEXT,                   -- path/url to serialised model file
    is_active       BOOLEAN DEFAULT FALSE,  -- only one model active at a time
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ml_model_registry_active
    ON "ML_MODEL_REGISTRY" (is_active) WHERE is_active = TRUE;

COMMENT ON TABLE "ML_MODEL_REGISTRY" IS
    'Trained model metadata, validation metrics, and artifact paths. Central registry for all ML models.';
