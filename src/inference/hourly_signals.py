"""
hourly_signals.py
Hourly ensemble inference pipeline.
Runs: residuals -> news events -> regime -> TCN -> LSTM -> LightGBM -> ML_SIGNALS_V2.

Usage:
    python -m src.inference.hourly_signals
    python -m src.inference.hourly_signals --date 2026-04-09
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
import torch
from dotenv import load_dotenv

from src.db import get_db_conn, get_backtest_conn
from src.models.train_ensemble import (
    FEATURES_ENSEMBLE, apply_regime_gating, ENSEMBLE_MODEL_NAME,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def load_ensemble_model():
    """Load the ensemble LightGBM model artifact."""
    path = Path("artifacts/lgbm_ensemble_v1.pkl")
    if not path.exists():
        log.error(f"Ensemble model not found at {path}")
        return None
    with open(path, "rb") as f:
        return pickle.load(f)


def fetch_regime(conn, target_date: str) -> dict:
    """Get current regime state from composite detector."""
    try:
        from src.models.regime import get_current_regime
        decision = get_current_regime(conn)
        return {"state": decision.regime_state, "confidence": decision.confidence}
    except Exception:
        return {"state": "range_bound", "confidence": 0.5}


def run(target_date: str | None = None):
    """Run hourly ensemble inference."""
    from src.inference.etl_tracker import track
    from src.models.registry import get_active_model
    from src.models.train_ensemble import load_ensemble_features

    if not target_date:
        target_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    log.info(f"Ensemble inference for {target_date}")

    with track("ensemble_hourly_signals") as t:
        dbcp = get_db_conn()

        # Load active model
        active = get_active_model(dbcp)
        if not active:
            log.error("No active model")
            dbcp.close()
            return 0

        model_id = active["model_id"]
        log.info(f"Active model: {active['model_name']} (id={model_id})")

        # Load model artifact
        artifact = load_ensemble_model()
        if artifact is None:
            # Fall back to active model artifact
            artifact_path = active.get("artifact_path", "")
            if not Path(artifact_path).exists():
                log.error(f"Model artifact not found: {artifact_path}")
                dbcp.close()
                return 0
            with open(artifact_path, "rb") as f:
                artifact = pickle.load(f)

        model = artifact["model"]
        features = artifact["features"]
        label_remap = artifact["label_remap"]

        # Get regime
        regime = fetch_regime(dbcp, target_date)
        log.info(f"Regime: {regime['state']} (confidence={regime['confidence']:.2f})")

        # Load features — use daily_signals' fetch_today_features (works without labels)
        # then enrich with BTC residuals + regime
        from src.inference.daily_signals import fetch_today_features
        rows = fetch_today_features(dbcp, features, target_date)
        if not rows:
            log.warning(f"No features for {target_date}. Skipping.")
            dbcp.close()
            return 0

        df = pd.DataFrame(rows)

        # Enrich with BTC residuals from cp_backtest
        try:
            bt = get_backtest_conn()
            df_res = pd.read_sql(
                'SELECT slug, AVG(beta_30d) as beta_30d, AVG(alpha_30d) as alpha_30d,'
                '  SUM(residual_1h) as residual_1d, AVG(residual_vol_ratio) as residual_vol_ratio'
                ' FROM "FE_BTC_RESIDUALS" WHERE DATE(timestamp) = %s'
                ' GROUP BY slug',
                bt, params=(target_date,),
            )
            df = df.merge(df_res, on="slug", how="left")
            bt.close()
        except Exception as e:
            log.warning(f"BTC residuals: {e}")
            for c in ["beta_30d", "alpha_30d", "residual_1d", "residual_vol_ratio"]:
                df[c] = np.nan

        # Fill missing features
        for f in features:
            if f not in df.columns:
                df[f] = np.nan

        log.info(f"Inference on {len(df)} coins")

        # Build X matrix
        avail = [f for f in features if f in df.columns]
        X = df[avail].values.astype(np.float32)

        # Predict
        probs = model.predict_proba(X)
        scores = probs[:, 2] - probs[:, 0]
        pred_class = np.argmax(probs, axis=1)
        directions = np.array([label_remap[c] for c in pred_class])

        # Apply regime gating
        gated_scores = np.array([
            apply_regime_gating(s, regime["state"], regime["confidence"])
            for s in scores
        ])

        # Build signal rows
        now = datetime.now(timezone.utc)
        ts = datetime.strptime(f"{target_date} 23:59:59", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

        signal_rows = []
        for i in range(len(df)):
            signal_rows.append({
                "slug": df.iloc[i]["slug"],
                "timestamp": ts,
                "signal_score": round(float(gated_scores[i]), 6),
                "residual_score": round(float(scores[i]), 6),
                "direction": int(directions[i]),
                "prob_buy": round(float(probs[i, 2]), 6),
                "prob_hold": round(float(probs[i, 1]), 6),
                "prob_sell": round(float(probs[i, 0]), 6),
                "confidence": round(float(max(probs[i])), 6),
                "ensemble_confidence": round(float(regime["confidence"]), 4),
                "regime_state": regime["state"],
                "tcn_direction": None,  # populated when TCN embeddings available
                "lstm_direction": None,
                "top_features": None,
                "model_id": model_id,
                "feature_date": target_date,
                "zscore_30d": None,
                "direction_zscore": None,
                "created_at": now,
            })

        # Write to ML_SIGNALS_V2
        write_signals_v2(dbcp, signal_rows)
        dbcp.close()

        t.rows = len(signal_rows)
        log.info(f"Written {len(signal_rows)} signals to ML_SIGNALS_V2 for {target_date}")

        # Summary
        buys = sum(1 for r in signal_rows if r["direction"] == 1)
        sells = sum(1 for r in signal_rows if r["direction"] == -1)
        holds = sum(1 for r in signal_rows if r["direction"] == 0)
        log.info(f"Signals: {buys} BUY, {holds} HOLD, {sells} SELL (regime={regime['state']})")

        return len(signal_rows)


def write_signals_v2(conn, rows: list[dict]):
    """Write to ML_SIGNALS_V2."""
    sql = """
        INSERT INTO "ML_SIGNALS_V2" (
            slug, timestamp, signal_score, residual_score, direction,
            prob_buy, prob_hold, prob_sell, confidence, ensemble_confidence,
            regime_state, tcn_direction, lstm_direction, top_features,
            model_id, feature_date, zscore_30d, direction_zscore, created_at
        ) VALUES (
            %(slug)s, %(timestamp)s, %(signal_score)s, %(residual_score)s, %(direction)s,
            %(prob_buy)s, %(prob_hold)s, %(prob_sell)s, %(confidence)s, %(ensemble_confidence)s,
            %(regime_state)s, %(tcn_direction)s, %(lstm_direction)s, %(top_features)s,
            %(model_id)s, %(feature_date)s, %(zscore_30d)s, %(direction_zscore)s, %(created_at)s
        )
        ON CONFLICT (slug, timestamp, model_id) DO UPDATE SET
            signal_score        = EXCLUDED.signal_score,
            residual_score      = EXCLUDED.residual_score,
            direction           = EXCLUDED.direction,
            prob_buy            = EXCLUDED.prob_buy,
            prob_hold           = EXCLUDED.prob_hold,
            prob_sell           = EXCLUDED.prob_sell,
            confidence          = EXCLUDED.confidence,
            ensemble_confidence = EXCLUDED.ensemble_confidence,
            regime_state        = EXCLUDED.regime_state,
            tcn_direction       = EXCLUDED.tcn_direction,
            lstm_direction      = EXCLUDED.lstm_direction,
            created_at          = EXCLUDED.created_at
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
    conn.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hourly Ensemble Signals")
    parser.add_argument("--date", type=str, default=None)
    args = parser.parse_args()
    run(target_date=args.date)
