"""
registry.py
Read/write interface for ML_MODEL_REGISTRY and ML_BACKTEST_RESULTS.
"""

import json
import logging
import os
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)


def get_db_conn():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", 5432),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "require"),
    )


def register_model(
    model_name: str,
    model_type: str,
    target: str,
    features_used: list[str],
    train_from: str,
    train_to: str,
    val_from: str | None = None,
    val_to: str | None = None,
    universe: str | None = None,
    hyperparameters: dict | None = None,
    val_ic_1d: float | None = None,
    val_ic_3d: float | None = None,
    val_ic_7d: float | None = None,
    val_accuracy: float | None = None,
    val_sharpe: float | None = None,
    val_win_rate: float | None = None,
    artifact_path: str | None = None,
    notes: str | None = None,
) -> int:
    """
    Register a trained model. Returns model_id.
    ON CONFLICT updates metrics (re-train safe).
    """
    conn = get_db_conn()
    sql = """
        INSERT INTO "ML_MODEL_REGISTRY" (
            model_name, model_type, target, features_used, hyperparameters,
            train_from, train_to, val_from, val_to, universe,
            val_ic_1d, val_ic_3d, val_ic_7d, val_accuracy, val_sharpe, val_win_rate,
            artifact_path, notes, created_at
        ) VALUES (
            %(model_name)s, %(model_type)s, %(target)s, %(features_used)s, %(hyperparameters)s,
            %(train_from)s, %(train_to)s, %(val_from)s, %(val_to)s, %(universe)s,
            %(val_ic_1d)s, %(val_ic_3d)s, %(val_ic_7d)s, %(val_accuracy)s, %(val_sharpe)s, %(val_win_rate)s,
            %(artifact_path)s, %(notes)s, %(created_at)s
        )
        ON CONFLICT (model_name) DO UPDATE SET
            val_ic_1d      = EXCLUDED.val_ic_1d,
            val_ic_3d      = EXCLUDED.val_ic_3d,
            val_ic_7d      = EXCLUDED.val_ic_7d,
            val_accuracy   = EXCLUDED.val_accuracy,
            val_sharpe     = EXCLUDED.val_sharpe,
            val_win_rate   = EXCLUDED.val_win_rate,
            artifact_path  = EXCLUDED.artifact_path,
            notes          = EXCLUDED.notes,
            created_at     = EXCLUDED.created_at
        RETURNING model_id
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "model_name":      model_name,
            "model_type":      model_type,
            "target":          target,
            "features_used":   json.dumps(features_used),
            "hyperparameters": json.dumps(hyperparameters) if hyperparameters else None,
            "train_from":      train_from,
            "train_to":        train_to,
            "val_from":        val_from,
            "val_to":          val_to,
            "universe":        universe,
            "val_ic_1d":       val_ic_1d,
            "val_ic_3d":       val_ic_3d,
            "val_ic_7d":       val_ic_7d,
            "val_accuracy":    val_accuracy,
            "val_sharpe":      val_sharpe,
            "val_win_rate":    val_win_rate,
            "artifact_path":   artifact_path,
            "notes":           notes,
            "created_at":      datetime.now(timezone.utc),
        })
        model_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    log.info(f"Registered model '{model_name}' (model_id={model_id})")
    return model_id


def set_active_model(model_id: int):
    """Deactivate all models, activate the specified one."""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute('UPDATE "ML_MODEL_REGISTRY" SET is_active = FALSE')
        cur.execute('UPDATE "ML_MODEL_REGISTRY" SET is_active = TRUE WHERE model_id = %s', (model_id,))
    conn.commit()
    conn.close()
    log.info(f"Active model set to model_id={model_id}")


def get_active_model(conn) -> dict | None:
    """Return the active model row as a dict, or None."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute('SELECT * FROM "ML_MODEL_REGISTRY" WHERE is_active = TRUE LIMIT 1')
        row = cur.fetchone()
    return dict(row) if row else None


def save_backtest(
    model_id: int,
    backtest_from: str,
    backtest_to: str,
    universe: str,
    metrics: dict,
):
    """Write backtest metrics to ML_BACKTEST_RESULTS."""
    conn = get_db_conn()
    sql = """
        INSERT INTO "ML_BACKTEST_RESULTS" (
            model_id, backtest_from, backtest_to, universe,
            ic_1d, ic_3d, ic_7d, ic_3d_mean, ic_3d_std, icir,
            accuracy, precision_buy, recall_buy, f1_buy,
            sharpe, max_drawdown, total_return, win_rate,
            total_trades, avg_holding_days, notes, created_at
        ) VALUES (
            %(model_id)s, %(backtest_from)s, %(backtest_to)s, %(universe)s,
            %(ic_1d)s, %(ic_3d)s, %(ic_7d)s, %(ic_3d_mean)s, %(ic_3d_std)s, %(icir)s,
            %(accuracy)s, %(precision_buy)s, %(recall_buy)s, %(f1_buy)s,
            %(sharpe)s, %(max_drawdown)s, %(total_return)s, %(win_rate)s,
            %(total_trades)s, %(avg_holding_days)s, %(notes)s, %(created_at)s
        )
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "model_id":        model_id,
            "backtest_from":   backtest_from,
            "backtest_to":     backtest_to,
            "universe":        universe,
            "ic_1d":           metrics.get("ic_1d"),
            "ic_3d":           metrics.get("ic_3d"),
            "ic_7d":           metrics.get("ic_7d"),
            "ic_3d_mean":      metrics.get("ic_3d_mean"),
            "ic_3d_std":       metrics.get("ic_3d_std"),
            "icir":            metrics.get("icir"),
            "accuracy":        metrics.get("accuracy"),
            "precision_buy":   metrics.get("precision_buy"),
            "recall_buy":      metrics.get("recall_buy"),
            "f1_buy":          metrics.get("f1_buy"),
            "sharpe":          metrics.get("sharpe"),
            "max_drawdown":    metrics.get("max_drawdown"),
            "total_return":    metrics.get("total_return"),
            "win_rate":        metrics.get("win_rate"),
            "total_trades":    metrics.get("total_trades"),
            "avg_holding_days":metrics.get("avg_holding_days"),
            "notes":           metrics.get("notes"),
            "created_at":      datetime.now(timezone.utc),
        })
    conn.commit()
    conn.close()
    log.info(f"Saved backtest for model_id={model_id}: IC3d={metrics.get('ic_3d'):.4f}, Sharpe={metrics.get('sharpe'):.2f}")
