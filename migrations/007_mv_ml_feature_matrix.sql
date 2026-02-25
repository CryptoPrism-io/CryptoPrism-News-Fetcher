-- Migration 007: mv_ml_feature_matrix
-- Materialized view joining ALL feature tables into one training matrix.
-- Anchored on ML_LABELS (our table). All FE_* are LEFT JOINed read-only.
-- REFRESH MATERIALIZED VIEW CONCURRENTLY "mv_ml_feature_matrix" runs daily after FE_ update.
-- Written: 2026-02-25
--
-- Join key: slug + DATE(timestamp) across all tables.
-- FE_FEAR_GREED_CMC has no slug — joined on date only (market-wide feature).

CREATE MATERIALIZED VIEW IF NOT EXISTS "mv_ml_feature_matrix" AS
SELECT
    -- Identity
    lbl.slug,
    lbl.timestamp,
    lbl.close_price,

    -- ── PRICE FEATURES (FE_PCT_CHANGE) ──────────────────────────────────
    pct.m_pct_1d,
    pct.d_pct_cum_ret,
    pct.d_pct_var,
    pct.d_pct_cvar,
    pct.d_pct_vol_1d,

    -- ── MOMENTUM SIGNALS (FE_MOMENTUM_SIGNALS) ───────────────────────────
    mom.m_mom_roc_bin,
    mom."m_mom_williams_%_bin",
    mom.m_mom_smi_bin,
    mom.m_mom_cmo_bin,
    mom.m_mom_mom_bin,

    -- ── OSCILLATOR SIGNALS (FE_OSCILLATORS_SIGNALS) ──────────────────────
    osc.m_osc_macd_crossover_bin,
    osc.m_osc_cci_bin,
    osc.m_osc_adx_bin,
    osc.m_osc_uo_bin,
    osc.m_osc_ao_bin,
    osc.m_osc_trix_bin,

    -- ── TVV SIGNALS (FE_TVV_SIGNALS) ─────────────────────────────────────
    tvv.m_tvv_obv_1d_binary,
    tvv.d_tvv_sma9_18,
    tvv.d_tvv_ema9_18,
    tvv.d_tvv_sma21_108,
    tvv.d_tvv_ema21_108,
    tvv.m_tvv_cmf,

    -- ── METRICS SIGNALS (FE_METRICS_SIGNAL) ──────────────────────────────
    met.m_pct_1d_signal,
    met.d_pct_cum_ret_signal,
    met.d_met_ath_month_signal,
    met.d_market_cap_signal,
    met.d_met_coin_age_y_signal,

    -- ── DMV SCORES (FE_DMV_SCORES) ───────────────────────────────────────
    dmv."Durability_Score",
    dmv."Momentum_Score",
    dmv."Valuation_Score",

    -- ── RATIO SIGNALS (FE_RATIOS_SIGNALS) ────────────────────────────────
    rat.m_rat_alpha_bin,
    rat.d_rat_beta_bin,
    rat.v_rat_sharpe_bin,
    rat.v_rat_sortino_bin,
    rat.v_rat_teynor_bin,
    rat.v_rat_common_sense_bin,
    rat.v_rat_information_bin,
    rat.v_rat_win_loss_bin,
    rat.m_rat_win_rate_bin,
    rat.m_rat_ror_bin,
    rat.d_rat_pain_bin,

    -- ── FEAR & GREED (FE_FEAR_GREED_CMC) — market-wide ──────────────────
    fg.fear_greed_index,
    fg.sentiment          AS fear_greed_sentiment,

    -- ── NEWS SIGNALS (FE_NEWS_SIGNALS) — our Tier 1 output ──────────────
    ns.news_sentiment_1d,
    ns.news_sentiment_3d,
    ns.news_sentiment_7d,
    ns.news_sentiment_momentum,
    ns.news_volume_1d,
    ns.news_volume_3d,
    ns.news_volume_zscore_1d,
    ns.news_breaking_flag,
    ns.news_regulation_flag,
    ns.news_security_flag,
    ns.news_adoption_flag,
    ns.news_source_quality,
    ns.news_tier1_count_1d,

    -- ── LABELS (ML_LABELS) — training targets ────────────────────────────
    lbl.forward_ret_1d,
    lbl.forward_ret_3d,
    lbl.forward_ret_7d,
    lbl.forward_ret_14d,
    lbl.label_1d,
    lbl.label_3d,
    lbl.label_7d,
    lbl.label_14d,
    lbl.volatility_7d,
    lbl.volatility_30d

FROM "ML_LABELS" lbl

LEFT JOIN "FE_PCT_CHANGE" pct
    ON  pct.slug = lbl.slug
    AND DATE(pct.timestamp) = DATE(lbl.timestamp)

LEFT JOIN "FE_MOMENTUM_SIGNALS" mom
    ON  mom.slug = lbl.slug
    AND DATE(mom.timestamp) = DATE(lbl.timestamp)

LEFT JOIN "FE_OSCILLATORS_SIGNALS" osc
    ON  osc.slug = lbl.slug
    AND DATE(osc.timestamp) = DATE(lbl.timestamp)

LEFT JOIN "FE_TVV_SIGNALS" tvv
    ON  tvv.slug = lbl.slug
    AND DATE(tvv.timestamp) = DATE(lbl.timestamp)

LEFT JOIN "FE_METRICS_SIGNAL" met
    ON  met.slug = lbl.slug
    AND DATE(met.timestamp) = DATE(lbl.timestamp)

LEFT JOIN "FE_DMV_SCORES" dmv
    ON  dmv.slug = lbl.slug
    AND DATE(dmv.timestamp) = DATE(lbl.timestamp)

LEFT JOIN "FE_RATIOS_SIGNALS" rat
    ON  rat.slug = lbl.slug
    AND DATE(rat.timestamp) = DATE(lbl.timestamp)

LEFT JOIN "FE_FEAR_GREED_CMC" fg
    ON  DATE(fg.timestamp) = DATE(lbl.timestamp)

LEFT JOIN "FE_NEWS_SIGNALS" ns
    ON  ns.slug = lbl.slug
    AND DATE(ns.timestamp) = DATE(lbl.timestamp)
WITH DATA;

-- Index the materialized view for fast training queries
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_mlf_slug_ts
    ON "mv_ml_feature_matrix" (slug, timestamp);

CREATE INDEX IF NOT EXISTS idx_mv_mlf_timestamp
    ON "mv_ml_feature_matrix" (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_mv_mlf_label3d
    ON "mv_ml_feature_matrix" (label_3d, timestamp DESC);

COMMENT ON MATERIALIZED VIEW "mv_ml_feature_matrix" IS
    'Training feature matrix: joins all FE_* tables + ML_LABELS + FE_NEWS_SIGNALS. Refresh daily after FE_ pipeline. Read-only source for model training.';
