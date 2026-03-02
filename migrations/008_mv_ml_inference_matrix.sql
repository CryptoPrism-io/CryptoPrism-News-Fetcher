-- Migration 008: mv_ml_inference_matrix
-- Inference-only feature view — anchored on FE_PCT_CHANGE (not ML_LABELS).
-- Provides the same 54 feature columns as mv_ml_feature_matrix but WITHOUT
-- labels, so it always contains today/yesterday even before labels can be
-- computed (labels require 14 days of future price data and would never
-- exist for the current date).
--
-- Refreshed daily just before ML_SIGNALS inference in daily-ml-signals.yml.
-- Written: 2026-03-03

CREATE MATERIALIZED VIEW IF NOT EXISTS "mv_ml_inference_matrix" AS
SELECT
    -- Identity
    pct.slug,
    pct.timestamp,

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

    -- ── NEWS SIGNALS (FE_NEWS_SIGNALS) ───────────────────────────────────
    ns.news_sentiment_1d,
    ns.news_sentiment_3d,
    ns.news_sentiment_7d,
    ns.news_sentiment_momentum,
    ns.news_volume_1d,
    ns.news_volume_zscore_1d,
    ns.news_breaking_flag,
    ns.news_regulation_flag,
    ns.news_security_flag,
    ns.news_adoption_flag,
    ns.news_source_quality,
    ns.news_tier1_count_1d

FROM "FE_PCT_CHANGE" pct

LEFT JOIN "FE_MOMENTUM_SIGNALS" mom
    ON  mom.slug = pct.slug
    AND DATE(mom.timestamp) = DATE(pct.timestamp)

LEFT JOIN "FE_OSCILLATORS_SIGNALS" osc
    ON  osc.slug = pct.slug
    AND DATE(osc.timestamp) = DATE(pct.timestamp)

LEFT JOIN "FE_TVV_SIGNALS" tvv
    ON  tvv.slug = pct.slug
    AND DATE(tvv.timestamp) = DATE(pct.timestamp)

LEFT JOIN "FE_METRICS_SIGNAL" met
    ON  met.slug = pct.slug
    AND DATE(met.timestamp) = DATE(pct.timestamp)

LEFT JOIN "FE_DMV_SCORES" dmv
    ON  dmv.slug = pct.slug
    AND DATE(dmv.timestamp) = DATE(pct.timestamp)

LEFT JOIN "FE_RATIOS_SIGNALS" rat
    ON  rat.slug = pct.slug
    AND DATE(rat.timestamp) = DATE(pct.timestamp)

LEFT JOIN "FE_FEAR_GREED_CMC" fg
    ON  DATE(fg.timestamp) = DATE(pct.timestamp)

LEFT JOIN "FE_NEWS_SIGNALS" ns
    ON  ns.slug = pct.slug
    AND DATE(ns.timestamp) = DATE(pct.timestamp)

WITH DATA;

-- Indexes for fast inference date queries
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_mlinf_slug_ts
    ON "mv_ml_inference_matrix" (slug, timestamp);

CREATE INDEX IF NOT EXISTS idx_mv_mlinf_timestamp
    ON "mv_ml_inference_matrix" (timestamp DESC);

COMMENT ON MATERIALIZED VIEW "mv_ml_inference_matrix" IS
    'Inference feature matrix: same 54 features as mv_ml_feature_matrix but NO labels. '
    'Anchored on FE_PCT_CHANGE so today/yesterday always have rows. '
    'Refreshed daily before ML_SIGNALS inference.';
