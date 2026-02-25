"""
backtest.py
Walk-forward backtest of any trained LightGBM model.

Strategy:
  - Each day: rank all coins by prob_buy from model inference
  - Long top-N coins equally weighted, hold for `horizon` days
  - No shorting (long-only), no transaction costs (raw signal quality check)

Metrics:
  - IC  (Spearman rank-corr of signal vs forward return) per day + mean
  - ICIR = mean(IC) / std(IC)
  - Sharpe of the long-only top-N portfolio (annualised)
  - Hit rate (% days where top-N outperform bottom-N)
  - Max drawdown
  - Cumulative PnL vs BTC benchmark

Usage:
    python -m src.models.backtest --model-id 2 --top-n 10 --horizon 3
    python -m src.models.backtest --model-id 1 --top-n 20 --horizon 7
"""

import argparse
import json
import logging
import pickle
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from scipy import stats

from src.db import get_db_conn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────────────

def load_artifact(artifact_path: str):
    path = Path(artifact_path)
    if not path.exists():
        # try relative to repo root
        path = Path("artifacts") / Path(artifact_path).name
    with open(path, "rb") as f:
        return pickle.load(f)


def get_model_info(conn, model_id: int) -> dict:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute('SELECT * FROM "ML_MODEL_REGISTRY" WHERE model_id = %s', (model_id,))
        row = cur.fetchone()
    if not row:
        raise ValueError(f"model_id={model_id} not found in ML_MODEL_REGISTRY")
    return dict(row)


def load_feature_matrix(conn, features: list[str], from_date: str, to_date: str) -> pd.DataFrame:
    """Pull features + forward returns from MV. READ-ONLY."""
    ret_cols = ["forward_ret_1d", "forward_ret_3d", "forward_ret_7d",
                "forward_ret_14d", "label_3d"]
    all_cols = ["slug", "timestamp"] + features + ret_cols
    col_sql = ", ".join(f'"{c}"' for c in all_cols).replace("%", "%%")
    query = f"""
        SELECT {col_sql}
        FROM "mv_ml_feature_matrix"
        WHERE timestamp >= %s
          AND timestamp <= %s
          AND label_3d IS NOT NULL
        ORDER BY timestamp ASC, slug ASC
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, (f"{from_date} 00:00:00+00", f"{to_date} 23:59:59+00"))
        rows = cur.fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def run_inference(model, label_remap: dict, features: list[str], df: pd.DataFrame) -> pd.DataFrame:
    """Add prob_buy, prob_hold, prob_sell, direction columns to df."""
    X = df[features].values.astype(np.float32)
    probs = model.predict_proba(X)          # (N, 3)  classes: -1, 0, 1
    # class order from LGBMClassifier depends on training classes_ attribute
    classes = list(model.classes_)
    buy_idx  = classes.index(1)  if 1  in classes else 2
    hold_idx = classes.index(0)  if 0  in classes else 1
    sell_idx = classes.index(-1) if -1 in classes else 0

    df = df.copy()
    df["prob_buy"]  = probs[:, buy_idx]
    df["prob_hold"] = probs[:, hold_idx]
    df["prob_sell"] = probs[:, sell_idx]
    df["signal_score"] = df["prob_buy"] - df["prob_sell"]   # ∈ (-1, 1)
    return df


# ── metrics ──────────────────────────────────────────────────────────────────

def compute_daily_ic(df: pd.DataFrame, signal_col: str, return_col: str) -> pd.Series:
    """Compute Spearman IC per day."""
    ics = {}
    for ts, g in df.groupby("timestamp"):
        if len(g) < 5:
            continue
        mask = g[return_col].notna() & g[signal_col].notna()
        if mask.sum() < 5:
            continue
        ic, _ = stats.spearmanr(g.loc[mask, signal_col], g.loc[mask, return_col])
        ics[ts] = ic
    return pd.Series(ics)


def long_only_portfolio(df: pd.DataFrame, top_n: int, return_col: str) -> pd.Series:
    """
    Daily returns of a long-only top-N portfolio.
    Returns a Series indexed by timestamp.
    """
    daily_returns = {}
    for ts, g in df.groupby("timestamp"):
        g_sorted = g.nlargest(top_n, "signal_score")
        ret = g_sorted[return_col].mean()
        daily_returns[ts] = ret
    return pd.Series(daily_returns)


def btc_benchmark(conn, from_date: str, to_date: str) -> pd.Series:
    """Pull BTC 1d forward return from ML_LABELS as benchmark."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DATE(timestamp) as d, forward_ret_1d
            FROM "ML_LABELS"
            WHERE slug = 'bitcoin'
              AND timestamp >= %s AND timestamp <= %s
            ORDER BY timestamp
        """, (f"{from_date} 00:00:00+00", f"{to_date} 23:59:59+00"))
        rows = cur.fetchall()
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({r[0]: r[1] for r in rows})
    return s


def sharpe(returns: pd.Series, periods_per_year: int = 252) -> float:
    r = returns.dropna()
    if r.std() == 0 or len(r) < 2:
        return float("nan")
    return float(r.mean() / r.std() * np.sqrt(periods_per_year))


def max_drawdown(returns: pd.Series) -> float:
    cum = (1 + returns.fillna(0)).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    return float(dd.min())


def hit_rate(df: pd.DataFrame, top_n: int, return_col: str) -> float:
    """% of days top-N outperform bottom-N."""
    wins = 0
    total = 0
    for ts, g in df.groupby("timestamp"):
        if len(g) < top_n * 2:
            continue
        g_sorted = g.sort_values("signal_score", ascending=False)
        top_ret = g_sorted.head(top_n)[return_col].mean()
        bot_ret = g_sorted.tail(top_n)[return_col].mean()
        if pd.notna(top_ret) and pd.notna(bot_ret):
            wins += int(top_ret > bot_ret)
            total += 1
    return round(wins / total, 4) if total else float("nan")


def save_backtest(conn, model_id: int, metrics: dict):
    """Insert results into ML_BACKTEST_RESULTS."""
    sql = """
        INSERT INTO "ML_BACKTEST_RESULTS" (
            model_id, backtest_from, backtest_to, universe,
            ic_3d, ic_3d_mean, ic_3d_std, icir,
            sharpe, max_drawdown, win_rate,
            accuracy, precision_buy, recall_buy, f1_buy,
            avg_holding_days, notes, created_at
        ) VALUES (
            %(model_id)s, %(from_date)s, %(to_date)s, %(universe)s,
            %(ic_3d)s, %(ic_3d_mean)s, %(ic_3d_std)s, %(icir)s,
            %(sharpe)s, %(max_drawdown)s, %(win_rate)s,
            %(accuracy)s, %(precision_buy)s, %(recall_buy)s, %(f1_buy)s,
            %(avg_holding_days)s, %(notes)s, NOW()
        )
    """
    with conn.cursor() as cur:
        cur.execute(sql, metrics)
    conn.commit()


# ── main ─────────────────────────────────────────────────────────────────────

def run(model_id: int, top_n: int = 10, horizon: int = 3,
        from_date: str | None = None, to_date: str | None = None):

    conn = get_db_conn()

    info = get_model_info(conn, model_id)
    features = json.loads(info["features_used"]) if isinstance(info["features_used"], str) else info["features_used"]
    artifact  = load_artifact(info["artifact_path"])
    model       = artifact["model"]
    label_remap = artifact["label_remap"]

    log.info(f"Model: {info['model_name']} | features={len(features)} | top_n={top_n} | horizon={horizon}d")

    # Default to full MV range if not specified
    if not from_date:
        with conn.cursor() as cur:
            cur.execute('SELECT MIN(timestamp)::date, MAX(timestamp)::date FROM "mv_ml_feature_matrix"')
            row = cur.fetchone()
            from_date, to_date = str(row[0]), str(row[1])

    log.info(f"Backtest window: {from_date} → {to_date}")

    df = load_feature_matrix(conn, features, from_date, to_date)
    if df.empty:
        log.error("No data loaded. Check dates and MV content.")
        return

    log.info(f"Loaded {len(df):,} rows across {df['timestamp'].nunique()} days, {df['slug'].nunique()} coins")

    # Run inference
    df = run_inference(model, label_remap, features, df)

    # ── IC metrics ────────────────────────────────────────────────────────────
    ret_col_map = {1: "forward_ret_1d", 3: "forward_ret_3d", 7: "forward_ret_7d"}
    ret_col = ret_col_map.get(horizon, "forward_ret_3d")

    ic_series = compute_daily_ic(df, "signal_score", ret_col)
    ic_mean = round(float(ic_series.mean()), 4)
    ic_std  = round(float(ic_series.std()),  4)
    icir    = round(ic_mean / ic_std, 4) if ic_std > 0 else float("nan")

    log.info(f"IC-{horizon}d: mean={ic_mean:.4f}  std={ic_std:.4f}  ICIR={icir:.4f}")
    log.info(f"IC range: [{ic_series.min():.3f}, {ic_series.max():.3f}]  positive={( ic_series > 0).mean()*100:.1f}%")

    # ── Portfolio simulation ───────────────────────────────────────────────────
    portfolio_rets = long_only_portfolio(df, top_n, ret_col)
    sharpe_portfolio = sharpe(portfolio_rets)
    mdd = max_drawdown(portfolio_rets)
    hr  = hit_rate(df, top_n, ret_col)

    log.info(f"Top-{top_n} long portfolio  Sharpe={sharpe_portfolio:.2f}  MaxDD={mdd*100:.1f}%  HitRate={hr*100:.1f}%")

    # BTC benchmark
    btc = btc_benchmark(conn, from_date, to_date)
    btc_sharpe = sharpe(btc)
    log.info(f"BTC benchmark            Sharpe={btc_sharpe:.2f}")

    # ── Top coins on most recent date ─────────────────────────────────────────
    latest_ts = df["timestamp"].max()
    latest = df[df["timestamp"] == latest_ts].nlargest(15, "signal_score")
    log.info(f"\n{'='*60}")
    log.info(f"  TOP-15 BUY signals on {str(latest_ts)[:10]}")
    log.info(f"{'='*60}")
    for _, row in latest.iterrows():
        fwd = row[ret_col]
        fwd_str = f"actual_{horizon}d={fwd*100:+.1f}%" if pd.notna(fwd) else "actual=N/A"
        log.info(f"  {row['slug']:<25} score={row['signal_score']:+.4f}  prob_buy={row['prob_buy']:.3f}  {fwd_str}")

    # IC breakdown by quarter
    log.info(f"\n{'='*60}")
    log.info(f"  IC-{horizon}d breakdown by month")
    log.info(f"{'='*60}")
    ic_df = ic_series.reset_index()
    ic_df.columns = ["ts", "ic"]
    ic_df["month"] = pd.to_datetime(ic_df["ts"]).dt.to_period("M")
    for month, g in ic_df.groupby("month"):
        log.info(f"  {month}  IC={g['ic'].mean():+.4f}  (n={len(g)} days)")

    # ── Save to DB ─────────────────────────────────────────────────────────────
    notes = json.dumps({
        "top_n": top_n, "horizon": horizon,
        "btc_sharpe": round(btc_sharpe, 3),
        "ic_positive_pct": round(float((ic_series > 0).mean()), 4),
        "n_days": int(ic_series.count()),
        "n_coins_avg": int(df.groupby("timestamp")["slug"].count().mean()),
    })
    save_backtest(conn, model_id, {
        "model_id":        model_id,
        "from_date":       from_date,
        "to_date":         to_date,
        "universe":        f"top{top_n}_h{horizon}d",
        "ic_3d":           ic_mean if horizon == 3 else None,
        "ic_3d_mean":      ic_mean,
        "ic_3d_std":       ic_std,
        "icir":            icir if not np.isnan(icir) else None,
        "sharpe":          round(sharpe_portfolio, 3),
        "max_drawdown":    round(mdd * 100, 2),
        "win_rate":        hr,
        "accuracy":        None,
        "precision_buy":   None,
        "recall_buy":      None,
        "f1_buy":          None,
        "avg_holding_days": horizon,
        "notes":           notes,
    })
    log.info(f"Backtest saved to ML_BACKTEST_RESULTS (model_id={model_id})")

    log.info(f"\n{'='*60}")
    log.info(f"  SUMMARY  {info['model_name']}")
    log.info(f"{'='*60}")
    log.info(f"  Window:     {from_date}  →  {to_date}")
    log.info(f"  IC-{horizon}d:      {ic_mean:+.4f}  (ICIR={icir:.2f})")
    log.info(f"  Hit Rate:   {hr*100:.1f}%  (top-{top_n} vs bottom-{top_n})")
    log.info(f"  Portfolio Sharpe (top-{top_n}): {sharpe_portfolio:.2f}")
    log.info(f"  BTC Sharpe: {btc_sharpe:.2f}")
    log.info(f"  Max Drawdown: {mdd*100:.1f}%")
    log.info(f"{'='*60}")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Walk-forward backtest of LightGBM model")
    parser.add_argument("--model-id", type=int, default=2, help="model_id from ML_MODEL_REGISTRY")
    parser.add_argument("--top-n",    type=int, default=10, help="Long top-N coins per day")
    parser.add_argument("--horizon",  type=int, default=3,  choices=[1, 3, 7], help="Forward return horizon (days)")
    parser.add_argument("--from-date", type=str, default=None)
    parser.add_argument("--to-date",   type=str, default=None)
    args = parser.parse_args()
    run(model_id=args.model_id, top_n=args.top_n, horizon=args.horizon,
        from_date=args.from_date, to_date=args.to_date)
