"""
daily_signals.py
Loads active model from ML_MODEL_REGISTRY, runs inference on today's features,
writes results to ML_SIGNALS. Plugs into etl_runs via etl_tracker.

Usage:
    python -m src.inference.daily_signals
    python -m src.inference.daily_signals --date 2026-02-24
"""

import argparse
import json
import logging
import os
import pickle
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from src.db import get_db_conn


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)



def load_model_artifact(artifact_path: str) -> dict:
    path = Path(artifact_path)
    if not path.exists():
        raise FileNotFoundError(f"Model artifact not found: {artifact_path}")
    with open(path, "rb") as f:
        return pickle.load(f)


def fetch_today_features(conn, features: list[str], target_date: str) -> list[dict]:
    """
    Pull today's feature row per coin from mv_ml_feature_matrix.
    READ-ONLY materialized view.
    """
    col_sql = ", ".join(f'"{c}"' for c in ["slug", "timestamp"] + features).replace("%", "%%")
    query = f"""
        SELECT {col_sql}
        FROM "mv_ml_feature_matrix"
        WHERE DATE(timestamp) = %s
        ORDER BY slug
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, (target_date,))
        return cur.fetchall()


def get_shap_top5(model, X: np.ndarray, features: list[str]) -> list[dict]:
    """Compute SHAP values and return top-5 features driving the BUY signal."""
    try:
        import shap
        explainer = shap.TreeExplainer(model)
        sv = explainer.shap_values(X)   # (3, N, F)
        # BUY class = index 2; take mean abs per feature
        importance = sv[2].flatten() if X.shape[0] == 1 else sv[2].mean(axis=0)
        ranked = sorted(
            zip(features, importance.tolist()),
            key=lambda x: abs(x[1]),
            reverse=True,
        )[:5]
        return [{"feature": f, "shap": round(v, 5)} for f, v in ranked]
    except Exception:
        return []


def write_signals(conn, rows: list[dict]):
    sql = """
        INSERT INTO "ML_SIGNALS" (
            slug, timestamp, signal_score, direction,
            prob_buy, prob_hold, prob_sell, confidence,
            top_features, model_id, feature_date, created_at
        ) VALUES (
            %(slug)s, %(timestamp)s, %(signal_score)s, %(direction)s,
            %(prob_buy)s, %(prob_hold)s, %(prob_sell)s, %(confidence)s,
            %(top_features)s, %(model_id)s, %(feature_date)s, %(created_at)s
        )
        ON CONFLICT (slug, timestamp, model_id) DO UPDATE SET
            signal_score = EXCLUDED.signal_score,
            direction    = EXCLUDED.direction,
            prob_buy     = EXCLUDED.prob_buy,
            prob_hold    = EXCLUDED.prob_hold,
            prob_sell    = EXCLUDED.prob_sell,
            confidence   = EXCLUDED.confidence,
            top_features = EXCLUDED.top_features,
            created_at   = EXCLUDED.created_at
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
    conn.commit()


def run(target_date: str | None = None):
    from src.models.registry import get_active_model
    from src.inference.etl_tracker import track

    if not target_date:
        target_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    log.info(f"Running daily signals for {target_date}")

    with track("ml_daily_signals") as t:
        conn = get_db_conn()

        # Get active model
        active = get_active_model(conn)
        if not active:
            log.error("No active model in ML_MODEL_REGISTRY. Train a model first.")
            conn.close()
            return 0

        model_id = active["model_id"]
        features = json.loads(active["features_used"]) if isinstance(active["features_used"], str) else active["features_used"]
        artifact_path = active["artifact_path"]

        log.info(f"Active model: {active['model_name']} (id={model_id})")

        # Load artifact
        artifact = load_model_artifact(artifact_path)
        model       = artifact["model"]
        label_remap = artifact["label_remap"]   # {0: -1, 1: 0, 2: 1}

        # Fetch features
        rows = fetch_today_features(conn, features, target_date)
        if not rows:
            log.warning(f"No feature rows found for {target_date}. Skipping.")
            conn.close()
            return 0

        log.info(f"Running inference on {len(rows)} coins")

        # Build X matrix
        import pandas as pd
        df = pd.DataFrame(rows)
        X = df[features].values.astype(np.float32)

        # Predict
        probs = model.predict_proba(X)            # shape (N, 3): [P(sell), P(hold), P(buy)]
        pred_class = np.argmax(probs, axis=1)
        directions = np.array([label_remap[c] for c in pred_class])
        signal_scores = probs[:, 2] - probs[:, 0]  # P(buy) - P(sell)

        # SHAP for a sample (top-20 by signal score)
        top20_idx = np.argsort(signal_scores)[-20:]

        signal_rows = []
        now = datetime.now(timezone.utc)
        # Daily timestamp at 23:59:59 UTC matching FE_ convention
        ts = datetime.strptime(f"{target_date} 23:59:59", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

        for i, row in enumerate(rows):
            prob_sell, prob_hold, prob_buy = float(probs[i, 0]), float(probs[i, 1]), float(probs[i, 2])
            shap_feats = []
            if i in top20_idx:
                shap_feats = get_shap_top5(model, X[i:i+1], features)

            signal_rows.append({
                "slug":         row["slug"],
                "timestamp":    ts,
                "signal_score": round(float(signal_scores[i]), 6),
                "direction":    int(directions[i]),
                "prob_buy":     round(prob_buy, 6),
                "prob_hold":    round(prob_hold, 6),
                "prob_sell":    round(prob_sell, 6),
                "confidence":   round(max(prob_buy, prob_hold, prob_sell), 6),
                "top_features": json.dumps(shap_feats) if shap_feats else None,
                "model_id":     model_id,
                "feature_date": target_date,
                "created_at":   now,
            })

        write_signals(conn, signal_rows)
        conn.close()

        t.rows = len(signal_rows)
        log.info(f"Written {len(signal_rows)} signals to ML_SIGNALS for {target_date}")

        # Log top-10 BUY signals
        buys = sorted(
            [r for r in signal_rows if r["direction"] == 1],
            key=lambda x: -x["signal_score"]
        )[:10]
        if buys:
            log.info("Top-10 BUY signals:")
            for b in buys:
                log.info(f"  {b['slug']:20s}  score={b['signal_score']:+.4f}  conf={b['confidence']:.3f}")

        return len(signal_rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run daily ML inference → ML_SIGNALS")
    parser.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD (default: yesterday)")
    args = parser.parse_args()
    run(target_date=args.date)
