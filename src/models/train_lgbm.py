"""
train_lgbm.py
LightGBM baseline trainer — reads features from cp_backtest + labels from dbcp, writes to ML_MODEL_REGISTRY.

Two modes:
  --mode price_only    : Block A features only (full 13yr OHLCV history)
  --mode news_augmented: Block A + Block B news features (128-day overlap)

Walk-forward split — NEVER random split (time-series correctness).

Usage:
    python -m src.models.train_lgbm --mode price_only
    python -m src.models.train_lgbm --mode news_augmented
"""

import argparse
import json
import logging
import os
import pickle
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from src.db import get_db_conn, get_backtest_conn


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Feature sets ─────────────────────────────────────────────────────────────

FEATURES_PRICE_ONLY = [
    # FE_PCT_CHANGE
    "m_pct_1d", "d_pct_cum_ret", "d_pct_var", "d_pct_cvar", "d_pct_vol_1d",
    # FE_MOMENTUM_SIGNALS
    "m_mom_roc_bin", "m_mom_williams_%_bin", "m_mom_smi_bin", "m_mom_cmo_bin", "m_mom_mom_bin",
    # FE_OSCILLATORS_SIGNALS
    "m_osc_macd_crossover_bin", "m_osc_cci_bin", "m_osc_adx_bin",
    "m_osc_uo_bin", "m_osc_ao_bin", "m_osc_trix_bin",
    # FE_TVV_SIGNALS
    "m_tvv_obv_1d_binary", "d_tvv_sma9_18", "d_tvv_ema9_18",
    "d_tvv_sma21_108", "d_tvv_ema21_108", "m_tvv_cmf",
    # FE_RATIOS_SIGNALS
    "m_rat_alpha_bin", "d_rat_beta_bin", "v_rat_sharpe_bin", "v_rat_sortino_bin",
    "v_rat_teynor_bin", "v_rat_common_sense_bin", "v_rat_information_bin",
    "v_rat_win_loss_bin", "m_rat_win_rate_bin", "m_rat_ror_bin", "d_rat_pain_bin",
    # Market context
    "fear_greed_index",
]

FEATURES_NEWS_AUGMENTED = FEATURES_PRICE_ONLY + [
    # FE_NEWS_SIGNALS
    "news_sentiment_1d", "news_sentiment_3d", "news_sentiment_7d",
    "news_sentiment_momentum", "news_volume_1d", "news_volume_zscore_1d",
    "news_breaking_flag", "news_regulation_flag", "news_security_flag",
    "news_adoption_flag", "news_source_quality", "news_tier1_count_1d",
]

TARGET_COL   = "label_3d"
LABEL_COLS   = ["label_1d", "label_3d", "label_7d", "label_14d"]
RETURN_COLS  = ["forward_ret_1d", "forward_ret_3d", "forward_ret_7d", "forward_ret_14d"]

# Earliest date with news sentiment data (fixed — start of news pipeline)
NEWS_DATA_START = "2025-10-21"


def compute_splits(mode: str) -> dict:
    """
    Rolling walk-forward splits anchored to today.

    Labels require future price data:
      label_3d  → 3 days forward  (our training target)
      label_14d → 14 days forward (used in full_eval metrics)

    We use a 14-day lag for test_to so all label columns are clean.
    Window sizes:
      test  : 14 days
      val   : 21 days
      train : from data-start to val_from - 1 day
    """
    today = date.today()
    test_to   = today - timedelta(days=14)          # latest date with all labels clean
    test_from = test_to - timedelta(days=13)        # 14-day test window
    val_to    = test_from - timedelta(days=1)
    val_from  = val_to - timedelta(days=20)         # 21-day val window
    train_to  = val_from - timedelta(days=1)
    train_from = (
        NEWS_DATA_START if mode == "news_augmented"
        else str(today - timedelta(days=730))       # price_only: rolling 2-year window
    )

    fmt = lambda d: d.strftime("%Y-%m-%d")          # noqa: E731
    splits = {
        "train_from": train_from,
        "train_to":   fmt(train_to),
        "val_from":   fmt(val_from),
        "val_to":     fmt(val_to),
        "test_from":  fmt(test_from),
        "test_to":    fmt(test_to),
    }
    log.info(
        f"Rolling splits ({mode}): "
        f"train={splits['train_from']}→{splits['train_to']}  "
        f"val={splits['val_from']}→{splits['val_to']}  "
        f"test={splits['test_from']}→{splits['test_to']}"
    )
    return splits

LGBM_PARAMS = {
    "objective":        "multiclass",
    "num_class":        3,
    "metric":           "multi_logloss",
    "n_estimators":     500,
    "learning_rate":    0.05,
    "num_leaves":       63,
    "min_child_samples": 20,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "reg_alpha":        0.1,
    "reg_lambda":       0.1,
    "n_jobs":           -1,
    "random_state":     42,
    "verbose":          -1,
}



def load_feature_matrix(dbcp_conn, features: list[str], from_date: str, to_date: str,
                        bt_conn=None) -> pd.DataFrame:
    """
    Load training feature matrix.

    When bt_conn (cp_backtest) is provided, uses dual-DB approach:
      - Labels from dbcp (ML_LABELS)
      - Price features from cp_backtest (full history, deduped)
      - Supplementary features from dbcp (fear-greed, news, DMV)

    When bt_conn is None, falls back to mv_ml_feature_matrix on dbcp.
    """
    ts_from = f"{from_date} 00:00:00+00"
    ts_to   = f"{to_date} 23:59:59+00"

    if bt_conn is None:
        # Fallback: MV on dbcp (features may be sparse if FE tables are 1-date)
        all_cols = ["slug", "timestamp"] + features + LABEL_COLS + RETURN_COLS
        col_sql = ", ".join(f'"{c}"' for c in all_cols).replace("%", "%%")
        query = f"""
            SELECT {col_sql}
            FROM "mv_ml_feature_matrix"
            WHERE timestamp >= %s AND timestamp <= %s AND label_3d IS NOT NULL
            ORDER BY timestamp ASC
        """
        with dbcp_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, (ts_from, ts_to))
            rows = cur.fetchall()
        df = pd.DataFrame(rows)
        log.info(f"Loaded {len(df):,} rows from mv_ml_feature_matrix ({from_date} → {to_date})")
        return df

    # ── Dual-DB approach ─────────────────────────────────────────────────

    # 1. Labels from dbcp
    label_cols_sql = ", ".join(
        f'"{c}"' for c in ["slug", "timestamp"] + LABEL_COLS + RETURN_COLS
    )
    df = pd.read_sql(
        f'SELECT {label_cols_sql} FROM "ML_LABELS"'
        f' WHERE timestamp >= %s AND timestamp <= %s AND label_3d IS NOT NULL'
        f' ORDER BY timestamp',
        dbcp_conn, params=(ts_from, ts_to),
    )
    if df.empty:
        log.info(f"Loaded 0 rows from ML_LABELS ({from_date} → {to_date})")
        return df

    df["_date"] = pd.to_datetime(df["timestamp"]).dt.date

    # 2. Price features from cp_backtest (deduped per slug+date)
    fe_tables = {
        "FE_PCT_CHANGE": [
            "m_pct_1d", "d_pct_cum_ret", "d_pct_var", "d_pct_cvar", "d_pct_vol_1d",
        ],
        "FE_MOMENTUM_SIGNALS": [
            "m_mom_roc_bin", "m_mom_williams_%_bin", "m_mom_smi_bin",
            "m_mom_cmo_bin", "m_mom_mom_bin",
        ],
        "FE_OSCILLATORS_SIGNALS": [
            "m_osc_macd_crossover_bin", "m_osc_cci_bin", "m_osc_adx_bin",
            "m_osc_uo_bin", "m_osc_ao_bin", "m_osc_trix_bin",
        ],
        "FE_TVV_SIGNALS": [
            "m_tvv_obv_1d_binary", "d_tvv_sma9_18", "d_tvv_ema9_18",
            "d_tvv_sma21_108", "d_tvv_ema21_108", "m_tvv_cmf",
        ],
        "FE_RATIOS_SIGNALS": [
            "m_rat_alpha_bin", "d_rat_beta_bin", "v_rat_sharpe_bin",
            "v_rat_sortino_bin", "v_rat_teynor_bin", "v_rat_common_sense_bin",
            "v_rat_information_bin", "v_rat_win_loss_bin", "m_rat_win_rate_bin",
            "m_rat_ror_bin", "d_rat_pain_bin",
        ],
    }

    for table, cols in fe_tables.items():
        col_sql = ", ".join(f'"{c}"' for c in cols).replace("%", "%%")
        try:
            df_fe = pd.read_sql(
                f'SELECT DISTINCT ON (slug, DATE(timestamp))'
                f'  slug, DATE(timestamp) AS _date, {col_sql}'
                f' FROM "{table}"'
                f' WHERE timestamp >= %s AND timestamp <= %s'
                f' ORDER BY slug, DATE(timestamp), timestamp DESC',
                bt_conn, params=(ts_from, ts_to),
            )
            df = df.merge(df_fe, on=["slug", "_date"], how="left")
            log.info(f"  {table}: {len(df_fe):,} feature rows merged")
        except Exception as e:
            log.warning(f"  {table}: unavailable ({e}), filling NaN")
            for c in cols:
                df[c] = np.nan

    # 3. Supplementary from dbcp
    # Fear & Greed (market-wide, no slug)
    try:
        df_fg = pd.read_sql(
            'SELECT DATE(timestamp) AS _date, fear_greed_index'
            ' FROM "FE_FEAR_GREED_CMC"'
            ' WHERE timestamp >= %s AND timestamp <= %s',
            dbcp_conn, params=(ts_from, ts_to),
        )
        df = df.merge(df_fg, on="_date", how="left")
    except Exception:
        df["fear_greed_index"] = np.nan

    # News signals from dbcp
    news_cols = [
        "news_sentiment_1d", "news_sentiment_3d", "news_sentiment_7d",
        "news_sentiment_momentum", "news_volume_1d", "news_volume_zscore_1d",
        "news_breaking_flag", "news_regulation_flag", "news_security_flag",
        "news_adoption_flag", "news_source_quality", "news_tier1_count_1d",
    ]
    try:
        ncol_sql = ", ".join(news_cols)
        df_news = pd.read_sql(
            f'SELECT slug, DATE(timestamp) AS _date, {ncol_sql}'
            f' FROM "FE_NEWS_SIGNALS"'
            f' WHERE timestamp >= %s AND timestamp <= %s',
            dbcp_conn, params=(ts_from, ts_to),
        )
        df = df.merge(df_news, on=["slug", "_date"], how="left")
    except Exception:
        for c in news_cols:
            df[c] = np.nan

    df.drop(columns=["_date"], inplace=True)

    n_feats = sum(1 for f in features if f in df.columns and df[f].notna().any())
    log.info(f"Loaded {len(df):,} rows via dual-DB ({from_date} → {to_date}), "
             f"{n_feats}/{len(features)} features with data")
    return df


def prepare_xy(df: pd.DataFrame, features: list[str]):
    """Return X (features), y (label_3d as 0/1/2 for LightGBM), plus raw arrays."""
    # LightGBM needs 0-indexed classes: map -1→0, 0→1, 1→2
    label_map = {-1: 0, 0: 1, 1: 2}
    y = df[TARGET_COL].map(label_map).values.astype(int)

    # Use only available columns (news cols may be NaN in price-only mode — LGB handles NaN)
    avail = [f for f in features if f in df.columns]
    X = df[avail].values.astype(np.float32)

    return X, y, avail


def train(mode: str = "price_only"):
    try:
        import lightgbm as lgb
    except ImportError:
        log.error("lightgbm not installed. Run: pip install lightgbm")
        raise

    from src.models.evaluate import full_eval
    from src.models.registry import register_model, save_backtest, set_active_model

    split    = compute_splits(mode)
    features = FEATURES_NEWS_AUGMENTED if mode == "news_augmented" else FEATURES_PRICE_ONLY
    model_name = f"lgbm_{mode}_v1"

    conn = get_db_conn()

    # Use cp_backtest for features if DB_BACKTEST_NAME is set (full historical FE data)
    bt_conn = None
    backtest_name = os.environ.get("DB_BACKTEST_NAME", "").strip()
    if backtest_name:
        bt_conn = get_backtest_conn()
        log.info(f"Using dual-DB: features from {backtest_name}, labels from dbcp")

    # Load train + val + test sets
    df_train = load_feature_matrix(conn, features, split["train_from"], split["train_to"], bt_conn)
    df_val   = load_feature_matrix(conn, features, split["val_from"],   split["val_to"],   bt_conn)
    df_test  = load_feature_matrix(conn, features, split["test_from"],  split["test_to"],  bt_conn)
    if bt_conn:
        bt_conn.close()
    conn.close()

    if df_train.empty:
        log.error("Training set is empty — run labels.py and refresh_mv.py first.")
        return

    if df_val.empty or df_test.empty:
        log.error(
            f"Val ({len(df_val)} rows) or test ({len(df_test)} rows) set is empty. "
            f"ML_LABELS likely missing for val={split['val_from']}→{split['val_to']} "
            f"or test={split['test_from']}→{split['test_to']}. "
            f"Run: python -m src.features.labels --from-date {split['val_from']}"
        )
        raise SystemExit(1)

    X_train, y_train, used_features = prepare_xy(df_train, features)
    X_val,   y_val,   _             = prepare_xy(df_val,   features)
    X_test,  y_test,  _             = prepare_xy(df_test,  features)

    log.info(f"Train: {X_train.shape} | Val: {X_val.shape} | Test: {X_test.shape}")
    log.info(f"Features: {len(used_features)}")

    # Train
    model = lgb.LGBMClassifier(**LGBM_PARAMS)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(100)],
    )

    # Predict probabilities on val and test
    label_remap = {0: -1, 1: 0, 2: 1}   # back to -1/0/1
    def predict(X, df):
        probs  = model.predict_proba(X)                    # shape (N, 3): [P(sell), P(hold), P(buy)]
        pred_class = np.argmax(probs, axis=1)
        pred_label = np.array([label_remap[c] for c in pred_class])
        signal = probs[:, 2] - probs[:, 0]                 # P(buy) - P(sell) → continuous -1 to +1
        true_label = df[TARGET_COL].values
        return signal, pred_label, true_label

    sig_val,  pred_val,  true_val  = predict(X_val,  df_val)
    sig_test, pred_test, true_test = predict(X_test, df_test)

    dates_val  = df_val["timestamp"].dt.date.astype(str).values
    dates_test = df_test["timestamp"].dt.date.astype(str).values

    val_metrics = full_eval(
        sig_val, true_val, pred_val,
        df_val["forward_ret_1d"].values,
        df_val["forward_ret_3d"].values,
        df_val["forward_ret_7d"].values,
        dates_val,
    )
    test_metrics = full_eval(
        sig_test, true_test, pred_test,
        df_test["forward_ret_1d"].values,
        df_test["forward_ret_3d"].values,
        df_test["forward_ret_7d"].values,
        dates_test,
    )

    # SHAP feature importance
    try:
        import shap
        explainer  = shap.TreeExplainer(model)
        shap_vals  = explainer.shap_values(X_val[:500])  # sample for speed
        # shap_vals shape: (3, N, F) — use BUY class (index 2)
        importance = np.abs(shap_vals[2]).mean(axis=0)
        top5 = sorted(zip(used_features, importance), key=lambda x: -x[1])[:5]
        log.info(f"Top-5 SHAP features: {[(f, round(v, 4)) for f, v in top5]}")
    except Exception as e:
        log.warning(f"SHAP failed: {e}")

    # Save model artifact
    artifact_dir = Path("artifacts")
    artifact_dir.mkdir(exist_ok=True)
    artifact_path = str(artifact_dir / f"{model_name}.pkl")
    with open(artifact_path, "wb") as f:
        pickle.dump({"model": model, "features": used_features, "label_remap": label_remap}, f)
    log.info(f"Model saved to {artifact_path}")

    # Register
    model_id = register_model(
        model_name=model_name,
        model_type="lightgbm",
        target=TARGET_COL,
        features_used=used_features,
        train_from=split["train_from"],
        train_to=split["train_to"],
        val_from=split["val_from"],
        val_to=split["val_to"],
        universe="all_available",
        hyperparameters=LGBM_PARAMS,
        val_ic_1d=val_metrics["ic_1d"],
        val_ic_3d=val_metrics["ic_3d"],
        val_ic_7d=val_metrics["ic_7d"],
        val_accuracy=val_metrics["accuracy"],
        val_sharpe=val_metrics["sharpe"],
        val_win_rate=val_metrics["win_rate"],
        artifact_path=artifact_path,
        notes=f"mode={mode}",
    )

    save_backtest(model_id, split["test_from"], split["test_to"], "all_available", test_metrics)

    if mode == "news_augmented":
        # News-augmented replaces price-only as active model if IC improves
        log.info("Setting news_augmented model as active.")
        set_active_model(model_id)

    log.info(
        f"\n{'='*60}\n"
        f"  Model: {model_name}  (id={model_id})\n"
        f"  Val   IC-3d: {val_metrics['ic_3d']:.4f}  Sharpe: {val_metrics['sharpe']:.2f}\n"
        f"  Test  IC-3d: {test_metrics['ic_3d']:.4f}  Sharpe: {test_metrics['sharpe']:.2f}\n"
        f"{'='*60}"
    )
    return model_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train LightGBM model")
    parser.add_argument(
        "--mode", choices=["price_only", "news_augmented"], default="price_only",
        help="Feature set to use (default: price_only)"
    )
    args = parser.parse_args()
    train(mode=args.mode)
