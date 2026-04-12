"""
train_ensemble.py
Enhanced LightGBM with ~85 features + Ensemble Meta-Learner.
Combines: original price features + BTC residuals + TCN/LSTM embeddings + news events + regime.

Usage:
    python -m src.models.train_ensemble
"""

import argparse
import json
import logging
import os
import pickle
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
import torch
from dotenv import load_dotenv

from src.db import get_db_conn, get_backtest_conn, get_backtest_h_conn
from src.models.train_lgbm import (
    compute_splits, FEATURES_PRICE_ONLY, LGBM_PARAMS,
    TARGET_COL, LABEL_COLS, RETURN_COLS, NEWS_DATA_START,
    prepare_xy, get_top_universe_slugs,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Original 34 price features (without metrics/DMV)
FEATURES_ORIGINAL = FEATURES_PRICE_ONLY

# BTC Residual features
FEATURES_BTC_RESIDUAL = [
    "beta_30d", "alpha_30d", "residual_1d", "residual_vol_ratio",
]

# LSTM embeddings (12 dims + 2 probs)
FEATURES_LSTM = [f"lemb_{i}" for i in range(12)] + ["lstm_prob_buy", "lstm_prob_sell"]

# TCN embeddings (16 dims + 2 probs)
FEATURES_TCN = [f"emb_{i}" for i in range(16)] + ["tcn_prob_buy", "tcn_prob_sell"]

# News event features
FEATURES_NEWS_EVENTS = [
    "hours_since_listing", "hours_since_hack_exploit", "hours_since_regulatory",
    "hours_since_partnership", "hours_since_tokenomics", "hours_since_macro",
    "event_magnitude_est", "event_count_24h", "news_surprise", "cross_coin_news_ratio",
]

# News sentiment (from existing pipeline)
FEATURES_NEWS_SENTIMENT = [
    "news_sentiment_1d", "news_sentiment_3d", "news_sentiment_7d",
    "news_sentiment_momentum", "news_volume_1d", "news_volume_zscore_1d",
    "news_breaking_flag", "news_regulation_flag", "news_security_flag",
    "news_adoption_flag", "news_source_quality", "news_tier1_count_1d",
]

# BTC-relative context
FEATURES_BTC_CONTEXT = [
    "fear_greed_index", "btc_vol_7d", "btc_momentum_24h",
]

# Lunar cycle features (synodic month ≈ 29.53 days)
FEATURES_LUNAR = ["lunar_sin", "lunar_cos"]

FEATURES_ENSEMBLE = (
    FEATURES_ORIGINAL + FEATURES_BTC_RESIDUAL + FEATURES_LSTM +
    FEATURES_TCN + FEATURES_NEWS_EVENTS + FEATURES_NEWS_SENTIMENT +
    FEATURES_BTC_CONTEXT + FEATURES_LUNAR
)

ENSEMBLE_MODEL_NAME = "lgbm_ensemble_v1"
ARTIFACT_PATH = "artifacts/lgbm_ensemble_v1.pkl"
META_ARTIFACT_PATH = "artifacts/ensemble_meta_learner.pkl"


def apply_regime_gating(score: float, regime: str, confidence: float) -> float:
    """Adjust signal score based on market regime."""
    if regime == "risk_on":
        return score if score > 0 else score * (1 - confidence * 0.5)
    elif regime == "risk_off":
        return score if score < 0 else score * (1 - confidence * 0.5)
    elif regime == "choppy":
        return score * (1 - confidence * 0.3)
    elif regime == "breakout":
        return score * (1 + confidence * 0.2)
    return score


def load_ensemble_features(from_date: str, to_date: str) -> pd.DataFrame:
    """
    Load all feature sources and merge into single training DataFrame.
    Labels from dbcp, price features from cp_backtest, embeddings from cp_backtest,
    supplementary from dbcp.
    """
    dbcp = get_db_conn()
    bt = get_backtest_conn()

    ts_from = f"{from_date} 00:00:00+00"
    ts_to = f"{to_date} 23:59:59+00"

    # 1. Labels from dbcp
    label_cols_sql = ", ".join(f'"{c}"' for c in ["slug", "timestamp"] + LABEL_COLS + RETURN_COLS)
    df = pd.read_sql(
        f'SELECT {label_cols_sql} FROM "ML_LABELS"'
        f' WHERE timestamp >= %s AND timestamp <= %s AND label_3d IS NOT NULL'
        f' ORDER BY timestamp',
        dbcp, params=(ts_from, ts_to),
    )
    if df.empty:
        log.info(f"No labels for {from_date} to {to_date}")
        bt.close(); dbcp.close()
        return df

    # Universe filter: keep only top coins by market cap
    top_slugs = get_top_universe_slugs(bt)
    before_n = len(df)
    df = df[df["slug"].isin(top_slugs)]
    log.info(f"  Universe filter: {before_n:,} → {len(df):,} rows ({len(df['slug'].unique())} coins)")

    if df.empty:
        bt.close(); dbcp.close()
        return df

    df["_date"] = pd.to_datetime(df["timestamp"]).dt.date
    log.info(f"  Labels: {len(df):,} rows")

    # 2. Price features from cp_backtest (same as train_lgbm dual-DB)
    fe_tables = {
        "FE_PCT_CHANGE": ["m_pct_1d", "d_pct_cum_ret", "d_pct_var", "d_pct_cvar", "d_pct_vol_1d"],
        "FE_MOMENTUM_SIGNALS": ["m_mom_roc_bin", "m_mom_williams_%_bin", "m_mom_smi_bin", "m_mom_cmo_bin", "m_mom_mom_bin"],
        "FE_OSCILLATORS_SIGNALS": ["m_osc_macd_crossover_bin", "m_osc_cci_bin", "m_osc_adx_bin", "m_osc_uo_bin", "m_osc_ao_bin", "m_osc_trix_bin"],
        "FE_TVV_SIGNALS": ["m_tvv_obv_1d_binary", "d_tvv_sma9_18", "d_tvv_ema9_18", "d_tvv_sma21_108", "d_tvv_ema21_108", "m_tvv_cmf"],
        "FE_RATIOS_SIGNALS": ["m_rat_alpha_bin", "d_rat_beta_bin", "v_rat_sharpe_bin", "v_rat_sortino_bin", "v_rat_teynor_bin", "v_rat_common_sense_bin", "v_rat_information_bin", "v_rat_win_loss_bin", "m_rat_win_rate_bin", "m_rat_ror_bin", "d_rat_pain_bin"],
    }
    for table, cols in fe_tables.items():
        col_sql = ", ".join(f'"{c}"' for c in cols).replace("%", "%%")
        try:
            df_fe = pd.read_sql(
                f'SELECT DISTINCT ON (slug, DATE(timestamp))'
                f'  slug, DATE(timestamp) AS _date, {col_sql}'
                f' FROM "{table}" WHERE timestamp >= %s AND timestamp <= %s'
                f' ORDER BY slug, DATE(timestamp), timestamp DESC',
                bt, params=(ts_from, ts_to),
            )
            df = df.merge(df_fe, on=["slug", "_date"], how="left")
        except Exception as e:
            log.warning(f"  {table}: {e}")
            for c in cols: df[c] = np.nan

    # 3. BTC Residuals from cp_backtest
    try:
        df_res = pd.read_sql(
            'SELECT slug, DATE(timestamp) AS _date,'
            '  AVG(beta_30d) as beta_30d, AVG(alpha_30d) as alpha_30d,'
            '  SUM(residual_1h) as residual_1d, AVG(residual_vol_ratio) as residual_vol_ratio'
            ' FROM "FE_BTC_RESIDUALS" WHERE timestamp >= %s AND timestamp <= %s'
            ' GROUP BY slug, DATE(timestamp)',
            bt, params=(ts_from, ts_to),
        )
        df = df.merge(df_res, on=["slug", "_date"], how="left")
        log.info(f"  BTC residuals: {len(df_res):,} rows merged")
    except Exception as e:
        log.warning(f"  BTC residuals: {e}")
        for c in FEATURES_BTC_RESIDUAL: df[c] = np.nan

    # 4. LSTM embeddings from cp_backtest
    try:
        lemb_cols = ", ".join([f'"{c}"' for c in FEATURES_LSTM])
        df_lstm = pd.read_sql(
            f'SELECT slug, DATE(timestamp) AS _date, {lemb_cols}'
            f' FROM "ML_LSTM_EMBEDDINGS" WHERE timestamp >= %s AND timestamp <= %s',
            bt, params=(ts_from, ts_to),
        )
        df = df.merge(df_lstm, on=["slug", "_date"], how="left")
        log.info(f"  LSTM embeddings: {len(df_lstm):,} rows merged")
    except Exception as e:
        log.warning(f"  LSTM embeddings: {e}")
        for c in FEATURES_LSTM: df[c] = np.nan

    # 5. TCN embeddings from cp_backtest (aggregate hourly to daily)
    try:
        tcn_cols = ", ".join([f'AVG("{c}") as "{c}"' for c in FEATURES_TCN])
        df_tcn = pd.read_sql(
            f'SELECT slug, DATE(timestamp) AS _date, {tcn_cols}'
            f' FROM "ML_TCN_EMBEDDINGS" WHERE timestamp >= %s AND timestamp <= %s'
            f' GROUP BY slug, DATE(timestamp)',
            bt, params=(ts_from, ts_to),
        )
        df = df.merge(df_tcn, on=["slug", "_date"], how="left")
        log.info(f"  TCN embeddings: {len(df_tcn):,} rows merged")
    except Exception as e:
        log.warning(f"  TCN embeddings: {e}")
        for c in FEATURES_TCN: df[c] = np.nan

    bt.close()

    # 6. Supplementary from dbcp
    # Fear & Greed
    try:
        df_fg = pd.read_sql(
            'SELECT DATE(timestamp) AS _date, fear_greed_index FROM "FE_FEAR_GREED_CMC"'
            ' WHERE timestamp >= %s AND timestamp <= %s',
            dbcp, params=(ts_from, ts_to),
        )
        df = df.merge(df_fg, on="_date", how="left")
    except Exception:
        df["fear_greed_index"] = np.nan

    # News sentiment
    try:
        ncol = ", ".join(FEATURES_NEWS_SENTIMENT)
        df_news = pd.read_sql(
            f'SELECT slug, DATE(timestamp) AS _date, {ncol} FROM "FE_NEWS_SIGNALS"'
            f' WHERE timestamp >= %s AND timestamp <= %s',
            dbcp, params=(ts_from, ts_to),
        )
        df = df.merge(df_news, on=["slug", "_date"], how="left")
    except Exception:
        for c in FEATURES_NEWS_SENTIMENT: df[c] = np.nan

    # News events
    try:
        evt_cols = ", ".join([f'"{c}"' for c in FEATURES_NEWS_EVENTS if c != "event_magnitude_est"])
        df_evt = pd.read_sql(
            f'SELECT slug, DATE(timestamp) AS _date, magnitude_est as event_magnitude_est, {evt_cols}'
            f' FROM "FE_NEWS_EVENTS" WHERE timestamp >= %s AND timestamp <= %s',
            dbcp, params=(ts_from, ts_to),
        )
        df = df.merge(df_evt, on=["slug", "_date"], how="left")
        log.info(f"  News events: {len(df_evt):,} rows merged")
    except Exception as e:
        log.warning(f"  News events: {e}")
        for c in FEATURES_NEWS_EVENTS: df[c] = np.nan

    # BTC context features
    try:
        btc_ctx = pd.read_sql(
            'SELECT DATE(timestamp) AS _date,'
            '  (close / LAG(close) OVER (ORDER BY timestamp) - 1) as btc_momentum_24h'
            ' FROM "1K_coins_ohlcv" WHERE slug = \'bitcoin\''
            ' AND timestamp >= %s AND timestamp <= %s'
            ' ORDER BY timestamp',
            dbcp, params=(ts_from, ts_to),
        )
        df = df.merge(btc_ctx[["_date", "btc_momentum_24h"]], on="_date", how="left")
    except Exception:
        df["btc_momentum_24h"] = np.nan

    # BTC volatility
    try:
        btc_vol = pd.read_sql(
            'SELECT DATE(timestamp) AS _date, close FROM "1K_coins_ohlcv"'
            ' WHERE slug = \'bitcoin\' AND timestamp >= %s AND timestamp <= %s'
            ' ORDER BY timestamp',
            dbcp, params=((pd.Timestamp(from_date) - timedelta(days=35)).strftime("%Y-%m-%d 00:00:00+00"), ts_to),
        )
        btc_vol["ret"] = btc_vol["close"].pct_change()
        btc_vol["btc_vol_7d"] = btc_vol["ret"].rolling(7).std()
        df = df.merge(btc_vol[["_date", "btc_vol_7d"]], on="_date", how="left")
    except Exception:
        df["btc_vol_7d"] = np.nan

    dbcp.close()

    # Lunar cycle features (computed from timestamps, no DB needed)
    from src.features.lunar import compute_lunar_features
    lunar_sin, lunar_cos = compute_lunar_features(df["timestamp"])
    df["lunar_sin"] = lunar_sin
    df["lunar_cos"] = lunar_cos
    log.info(f"  Lunar features: computed for {len(df):,} rows")

    # Fill missing feature columns
    for f in FEATURES_ENSEMBLE:
        if f not in df.columns:
            df[f] = np.nan

    df.drop(columns=["_date"], inplace=True)

    n_feats = sum(1 for f in FEATURES_ENSEMBLE if f in df.columns and df[f].notna().any())
    log.info(f"Loaded {len(df):,} rows, {n_feats}/{len(FEATURES_ENSEMBLE)} features with data")
    return df


def train():
    """Train enhanced LightGBM + meta-learner."""
    try:
        import lightgbm as lgb
    except ImportError:
        log.error("lightgbm not installed")
        raise

    from src.models.evaluate import full_eval
    from src.models.registry import register_model, save_backtest, set_active_model

    split = compute_splits("news_augmented")
    model_name = ENSEMBLE_MODEL_NAME

    log.info("Loading ensemble features...")
    df_train = load_ensemble_features(split["train_from"], split["train_to"])
    df_val = load_ensemble_features(split["val_from"], split["val_to"])
    df_test = load_ensemble_features(split["test_from"], split["test_to"])

    if df_train.empty:
        log.error("Training set empty")
        return

    if df_val.empty or df_test.empty:
        log.error(f"Val ({len(df_val)}) or test ({len(df_test)}) empty")
        raise SystemExit(1)

    X_train, y_train, used_features = prepare_xy(df_train, FEATURES_ENSEMBLE)
    X_val, y_val, _ = prepare_xy(df_val, FEATURES_ENSEMBLE)
    X_test, y_test, _ = prepare_xy(df_test, FEATURES_ENSEMBLE)

    log.info(f"Train: {X_train.shape} | Val: {X_val.shape} | Test: {X_test.shape}")
    log.info(f"Features used: {len(used_features)}")

    # Train LightGBM
    model = lgb.LGBMClassifier(**LGBM_PARAMS)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(100)],
    )

    # Predict
    label_remap = {0: -1, 1: 0, 2: 1}

    def predict(X, df_src):
        probs = model.predict_proba(X)
        pred_class = np.argmax(probs, axis=1)
        pred_label = np.array([label_remap[c] for c in pred_class])
        scores = probs[:, 2] - probs[:, 0]
        return probs, pred_label, scores

    probs_val, pred_val, scores_val = predict(X_val, df_val)
    probs_test, pred_test, scores_test = predict(X_test, df_test)

    # Eval
    dates_val = df_val["timestamp"].values
    dates_test = df_test["timestamp"].values

    val_metrics = full_eval(
        scores_val, pred_val, df_val[TARGET_COL].values,
        df_val["forward_ret_1d"].values, df_val["forward_ret_3d"].values,
        df_val["forward_ret_7d"].values, dates_val,
    )
    test_metrics = full_eval(
        scores_test, pred_test, df_test[TARGET_COL].values,
        df_test["forward_ret_1d"].values, df_test["forward_ret_3d"].values,
        df_test["forward_ret_7d"].values, dates_test,
    )

    # Save
    artifact_dir = Path("artifacts")
    artifact_dir.mkdir(exist_ok=True)
    with open(ARTIFACT_PATH, "wb") as f:
        pickle.dump({"model": model, "features": used_features, "label_remap": label_remap}, f)
    log.info(f"Model saved to {ARTIFACT_PATH}")

    # Register
    model_id = register_model(
        model_name=model_name,
        model_type="lightgbm_ensemble",
        target=TARGET_COL,
        features_used=used_features,
        train_from=split["train_from"], train_to=split["train_to"],
        val_from=split["val_from"], val_to=split["val_to"],
        artifact_path=ARTIFACT_PATH,
        notes="ensemble: original + residuals + LSTM + TCN + news_events + regime",
    )

    save_backtest(
        model_id=model_id,
        backtest_from=split["test_from"], backtest_to=split["test_to"],
        universe="all_available",
        metrics=test_metrics,
    )

    set_active_model(model_id)

    log.info(f"\n{'='*60}")
    log.info(f"  Ensemble Model: {model_name} (id={model_id})")
    log.info(f"  Val  IC-3d: {val_metrics.get('ic_3d', 'N/A')}  Sharpe: {val_metrics.get('sharpe', 'N/A')}")
    log.info(f"  Test IC-3d: {test_metrics.get('ic_3d', 'N/A')}  Sharpe: {test_metrics.get('sharpe', 'N/A')}")
    log.info(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ensemble Trainer")
    parser.add_argument("--train", action="store_true", default=True)
    args = parser.parse_args()
    train()
