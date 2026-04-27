"""
Rolling Walk-Forward Backtest (Clean Out-of-Sample).

For each week in the trade period, trains a FRESH LightGBM model
using only data strictly before the trade week, generates signals
on unseen features, and simulates the 25-coin USDC portfolio with
Trailing-J exits.

This exactly mirrors the live Sunday retrain cycle — no look-ahead bias.
Run on GitHub Actions (DB-heavy, GCP-to-GCP).

Usage:
  python scripts/q1_rolling_backtest.py                       # default: Q1 2026
  python scripts/q1_rolling_backtest.py --start 2026-04-06 --end 2026-04-25 --label "April 2026"
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="lightgbm")

sys.stdout.reconfigure(line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

# ── Portfolio parameters (identical to live system) ──────────────────────────
CAPITAL = 5000.0
DEPLOY_PCT = 0.85
CURRENT_SL = -0.08
CURRENT_TP = 0.045
HOLD_DAYS = 3
LONG_N = 15
SHORT_N = 15

J_LONG_ACT = 0.02
J_LONG_TRAIL = -0.015
J_SHORT_ACT = 0.015
J_SHORT_TRAIL = -0.003

NEWS_DATA_START = "2025-10-21"  # legacy fallback — overridden by --train-start or DB query

USDC_COINS = {
    "bitcoin", "solana", "xrp", "dogecoin", "cardano",
    "chainlink", "avalanche-2", "litecoin", "bitcoin-cash", "uniswap",
    "hedera-hashgraph", "sui", "zcash", "aave", "arbitrum", "near",
    "neo", "curve-dao-token", "ethena",
    "worldcoin-wld", "dogwifcoin", "bonk", "pepe", "shiba-inu", "ordinals",
}

COIN_CATEGORIES = {
    "bitcoin": "L1-Major", "solana": "L1-Major",
    "xrp": "L1-Major", "cardano": "L1-Major",
    "litecoin": "L1-Major", "bitcoin-cash": "L1-Fork",
    "avalanche-2": "L1-Alt", "sui": "L1-Alt", "near": "L1-Alt",
    "neo": "L1-Alt", "hedera-hashgraph": "L1-Alt",
    "chainlink": "DeFi/Infra", "uniswap": "DeFi/Infra", "aave": "DeFi/Infra",
    "curve-dao-token": "DeFi/Infra", "ethena": "DeFi/Infra",
    "arbitrum": "L2", "zcash": "Privacy",
    "worldcoin-wld": "AI/Identity",
    "dogecoin": "Meme", "dogwifcoin": "Meme", "bonk": "Meme",
    "pepe": "Meme", "shiba-inu": "Meme", "ordinals": "Meme/BTC-Eco",
}

# ── Feature tables (cp_backtest has full history) ────────────────────────────
FE_TABLES_BT = {
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
    "FE_RESIDUAL_FEATURES": [
        "res_momentum_3d", "res_momentum_7d", "res_momentum_14d",
        "res_zscore_30d", "res_vol_regime",
        "res_autocorr_7d", "res_autocorr_14d", "res_volume_interaction",
    ],
    "FE_CROSS_COIN": [
        "cc_ret_rank_1d", "cc_ret_rank_7d", "cc_vol_rank_1d", "cc_mktcap_momentum",
        "cc_breadth_20d", "cc_advance_decline", "cc_dispersion", "cc_hhi_volume",
    ],
}

NEWS_COLS = [
    "news_sentiment_1d", "news_sentiment_3d", "news_sentiment_7d",
    "news_sentiment_momentum", "news_volume_1d", "news_volume_zscore_1d",
    "news_breaking_flag", "news_regulation_flag", "news_security_flag",
    "news_adoption_flag", "news_source_quality", "news_tier1_count_1d",
]

ALL_FEATURES = []
for _cols in FE_TABLES_BT.values():
    ALL_FEATURES.extend(_cols)
ALL_FEATURES.append("fear_greed_index")
ALL_FEATURES.extend(NEWS_COLS)

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


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_full_features(conn_dbcp, conn_bt, from_date, to_date):
    """Load feature matrix for full date range (dual-DB: labels from dbcp, FE from cp_backtest)."""
    ts_from = f"{from_date} 00:00:00+00"
    ts_to = f"{to_date} 23:59:59+00"

    print("  Loading ML_LABELS base from dbcp...")
    df = pd.read_sql(
        'SELECT slug, timestamp, label_3d, forward_ret_1d, forward_ret_3d, forward_ret_7d '
        'FROM "ML_LABELS" '
        'WHERE timestamp >= %s AND timestamp <= %s AND label_3d IS NOT NULL '
        'ORDER BY timestamp',
        conn_dbcp, params=(ts_from, ts_to),
    )
    df["_date"] = pd.to_datetime(df["timestamp"]).dt.date
    print(f"  Base: {len(df):,} rows, {df['slug'].nunique()} coins, "
          f"{df['_date'].min()} → {df['_date'].max()}")

    cur_bt = conn_bt.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    for table, cols in FE_TABLES_BT.items():
        escaped = [c.replace("%", "%%") for c in cols]
        col_sql = ", ".join(f'"{c}"' for c in escaped)
        try:
            cur_bt.execute(
                f'SELECT DISTINCT ON (slug, DATE(timestamp))'
                f'  slug, DATE(timestamp) AS _date, {col_sql}'
                f' FROM "{table}"'
                f' WHERE timestamp >= %s AND timestamp <= %s'
                f' ORDER BY slug, DATE(timestamp), timestamp DESC',
                (ts_from, ts_to),
            )
            rows = cur_bt.fetchall()
            df_fe = pd.DataFrame(rows)
            if not df_fe.empty:
                df = df.merge(df_fe, on=["slug", "_date"], how="left")
            else:
                for c in cols:
                    df[c] = np.nan
            print(f"  {table}: {len(df_fe):,} rows merged")
        except Exception as e:
            conn_bt.rollback()
            print(f"  {table}: FAILED ({e})")
            for c in cols:
                df[c] = np.nan
    cur_bt.close()

    try:
        df_fg = pd.read_sql(
            'SELECT DATE(timestamp) AS _date, fear_greed_index '
            'FROM "FE_FEAR_GREED_CMC" '
            'WHERE timestamp >= %s AND timestamp <= %s',
            conn_dbcp, params=(ts_from, ts_to),
        )
        df = df.merge(df_fg, on="_date", how="left")
        print(f"  FE_FEAR_GREED_CMC: {len(df_fg):,} rows merged")
    except Exception:
        df["fear_greed_index"] = np.nan

    try:
        ncol_sql = ", ".join(f'"{c}"' for c in NEWS_COLS)
        df_news = pd.read_sql(
            f'SELECT DISTINCT ON (slug, DATE(timestamp))'
            f'  slug, DATE(timestamp) AS _date, {ncol_sql}'
            f' FROM "FE_NEWS_SIGNALS"'
            f' WHERE timestamp >= %s AND timestamp <= %s'
            f' ORDER BY slug, DATE(timestamp), timestamp DESC',
            conn_dbcp, params=(ts_from, ts_to),
        )
        df = df.merge(df_news, on=["slug", "_date"], how="left")
        print(f"  FE_NEWS_SIGNALS: {len(df_news):,} rows merged")
    except Exception as e:
        print(f"  FE_NEWS_SIGNALS: FAILED ({e})")
        for c in NEWS_COLS:
            df[c] = np.nan

    for f in ALL_FEATURES:
        if f not in df.columns:
            df[f] = np.nan

    filled = sum(1 for f in ALL_FEATURES if f in df.columns and df[f].notna().any())
    print(f"  Features with data: {filled}/{len(ALL_FEATURES)}")
    return df


def load_inference_features(conn_dbcp, conn_bt, from_date, to_date):
    """Load feature matrix for inference — anchored on 1K_coins_ohlcv dates, no labels needed."""
    ts_from = f"{from_date} 00:00:00+00"
    ts_to = f"{to_date} 23:59:59+00"

    print("  Loading OHLCV date scaffold from dbcp (no labels needed)...")
    df = pd.read_sql(
        'SELECT DISTINCT slug, DATE(timestamp) AS _date '
        'FROM "1K_coins_ohlcv" '
        'WHERE timestamp >= %s AND timestamp <= %s '
        'ORDER BY _date',
        conn_dbcp, params=(ts_from, ts_to),
    )
    print(f"  Scaffold: {len(df):,} rows, {df['slug'].nunique()} coins, "
          f"{df['_date'].min()} → {df['_date'].max()}")

    cur_bt = conn_bt.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    for table, cols in FE_TABLES_BT.items():
        escaped = [c.replace("%", "%%") for c in cols]
        col_sql = ", ".join(f'"{c}"' for c in escaped)
        try:
            cur_bt.execute(
                f'SELECT DISTINCT ON (slug, DATE(timestamp))'
                f'  slug, DATE(timestamp) AS _date, {col_sql}'
                f' FROM "{table}"'
                f' WHERE timestamp >= %s AND timestamp <= %s'
                f' ORDER BY slug, DATE(timestamp), timestamp DESC',
                (ts_from, ts_to),
            )
            rows = cur_bt.fetchall()
            df_fe = pd.DataFrame(rows)
            if not df_fe.empty:
                df = df.merge(df_fe, on=["slug", "_date"], how="left")
            else:
                for c in cols:
                    df[c] = np.nan
            print(f"  {table}: {len(df_fe):,} rows merged")
        except Exception as e:
            conn_bt.rollback()
            print(f"  {table}: FAILED ({e})")
            for c in cols:
                df[c] = np.nan
    cur_bt.close()

    try:
        df_fg = pd.read_sql(
            'SELECT DATE(timestamp) AS _date, fear_greed_index '
            'FROM "FE_FEAR_GREED_CMC" '
            'WHERE timestamp >= %s AND timestamp <= %s',
            conn_dbcp, params=(ts_from, ts_to),
        )
        df = df.merge(df_fg, on="_date", how="left")
        print(f"  FE_FEAR_GREED_CMC: {len(df_fg):,} rows merged")
    except Exception:
        df["fear_greed_index"] = np.nan

    try:
        ncol_sql = ", ".join(f'"{c}"' for c in NEWS_COLS)
        df_news = pd.read_sql(
            f'SELECT DISTINCT ON (slug, DATE(timestamp))'
            f'  slug, DATE(timestamp) AS _date, {ncol_sql}'
            f' FROM "FE_NEWS_SIGNALS"'
            f' WHERE timestamp >= %s AND timestamp <= %s'
            f' ORDER BY slug, DATE(timestamp), timestamp DESC',
            conn_dbcp, params=(ts_from, ts_to),
        )
        df = df.merge(df_news, on=["slug", "_date"], how="left")
        print(f"  FE_NEWS_SIGNALS: {len(df_news):,} rows merged")
    except Exception as e:
        print(f"  FE_NEWS_SIGNALS: FAILED ({e})")
        for c in NEWS_COLS:
            df[c] = np.nan

    for f in ALL_FEATURES:
        if f not in df.columns:
            df[f] = np.nan

    filled = sum(1 for f in ALL_FEATURES if f in df.columns and df[f].notna().any())
    print(f"  Inference features with data: {filled}/{len(ALL_FEATURES)}")
    return df


def load_ohlcv(conn_h, coins, trade_start, trade_end):
    """Load hourly OHLCV for trade period + hold-period buffer."""
    ohlcv_from = (trade_start - timedelta(days=2)).isoformat()
    ohlcv_to = (trade_end + timedelta(days=7)).isoformat()
    cur = conn_h.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT slug, timestamp, open, high, low, close
        FROM ohlcv_1h_250_coins
        WHERE timestamp >= %s AND timestamp < %s
          AND slug = ANY(%s)
        ORDER BY slug, timestamp
    """, (ohlcv_from, ohlcv_to, list(coins)))
    rows = cur.fetchall()
    cur.close()
    ohlcv = defaultdict(list)
    for r in rows:
        ohlcv[r["slug"]].append(r)
    print(f"  {len(rows):,} hourly candles, {len(ohlcv)} coins")
    return ohlcv


def load_btc_benchmark(conn_dbcp, trade_start, trade_end):
    """Load BTC daily close for trade period."""
    btc_from = (trade_start - timedelta(days=2)).isoformat()
    btc_to = (trade_end + timedelta(days=2)).isoformat()
    cur = conn_dbcp.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT timestamp::date as d, close FROM "1K_coins_ohlcv"
        WHERE slug='bitcoin' AND timestamp >= %s AND timestamp < %s
        ORDER BY timestamp
    """, (btc_from, btc_to))
    rows = cur.fetchall()
    cur.close()
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
#  WALK-FORWARD SPLITS & TRAINING
# ═══════════════════════════════════════════════════════════════════════════════

def compute_splits(anchor_date, train_floor="2024-04-01", train_months=None):
    """Compute walk-forward splits as if today = anchor_date (mirrors live compute_splits)."""
    test_to = anchor_date - timedelta(days=14)
    test_from = test_to - timedelta(days=13)
    val_to = test_from - timedelta(days=1)
    val_from = val_to - timedelta(days=20)
    train_to = val_from - timedelta(days=1)
    fmt = lambda d: d.strftime("%Y-%m-%d")
    if train_months:
        rolling_start = train_to - timedelta(days=train_months * 30)
        effective_floor = max(rolling_start.isoformat(), train_floor)
    else:
        effective_floor = train_floor
    return {
        "train_from": effective_floor,
        "train_to": fmt(train_to),
        "val_from": fmt(val_from),
        "val_to": fmt(val_to),
    }


def get_retrain_sundays(trade_start, trade_end):
    """Sundays covering the trade period. First Sunday is on or before trade_start."""
    d = trade_start - timedelta(days=(trade_start.weekday() + 1) % 7)
    sundays = []
    while d <= trade_end:
        sundays.append(d)
        d += timedelta(days=7)
    return sundays


def train_model(df_all, split):
    """Train a fresh LightGBM on the given train/val split. Returns (model, features, diagnostics)."""
    d_train_from = date.fromisoformat(split["train_from"])
    d_train_to = date.fromisoformat(split["train_to"])
    d_val_from = date.fromisoformat(split["val_from"])
    d_val_to = date.fromisoformat(split["val_to"])

    df_train = df_all[(df_all["_date"] >= d_train_from) & (df_all["_date"] <= d_train_to)]
    df_val = df_all[(df_all["_date"] >= d_val_from) & (df_all["_date"] <= d_val_to)]

    if df_train.empty or df_val.empty:
        return None, None, {}

    label_map = {-1: 0, 0: 1, 1: 2}
    y_train = df_train["label_3d"].map(label_map).values.astype(int)
    y_val = df_val["label_3d"].map(label_map).values.astype(int)

    if len(np.unique(y_train)) < 3:
        return None, None, {}

    avail = [f for f in ALL_FEATURES if f in df_train.columns]
    X_train = df_train[avail].values.astype(np.float32)
    X_val = df_val[avail].values.astype(np.float32)

    model = lgb.LGBMClassifier(**LGBM_PARAMS)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(500)],
    )

    probs_val = model.predict_proba(X_val)
    classes = list(model.classes_)
    sig_val = probs_val[:, classes.index(2)] - probs_val[:, classes.index(0)]
    fwd_ret = df_val["forward_ret_3d"].values
    mask = ~np.isnan(fwd_ret) & ~np.isnan(sig_val)
    val_ic = float(np.corrcoef(sig_val[mask], fwd_ret[mask])[0, 1]) if mask.sum() > 10 else 0.0

    pred_class = np.argmax(probs_val, axis=1)
    remap = {0: -1, 1: 0, 2: 1}
    pred_labels = np.array([remap[c] for c in pred_class])
    true_labels = df_val["label_3d"].values
    val_acc = float(np.mean(pred_labels == true_labels))

    diagnostics = {
        "train_rows": int(len(df_train)),
        "val_rows": int(len(df_val)),
        "train_coins": int(df_train["slug"].nunique()),
        "val_coins": int(df_val["slug"].nunique()),
        "val_ic_3d": round(val_ic, 4),
        "val_accuracy": round(val_acc, 4),
        "best_iteration": int(model.best_iteration_),
        "train_from": split["train_from"],
        "train_to": split["train_to"],
        "val_from": split["val_from"],
        "val_to": split["val_to"],
    }
    return model, avail, diagnostics


# ═══════════════════════════════════════════════════════════════════════════════
#  INFERENCE
# ═══════════════════════════════════════════════════════════════════════════════

def generate_signals(model, features, df_train, df_infer, trade_from, trade_to):
    """Run model inference on trade-week features. Returns {date: [signal_dicts]}.
    Uses df_infer (OHLCV-anchored) for dates beyond ML_LABELS, falls back to df_train."""
    df_week = df_infer[(df_infer["_date"] >= trade_from) & (df_infer["_date"] <= trade_to)].copy()
    if df_week.empty:
        df_week = df_train[(df_train["_date"] >= trade_from) & (df_train["_date"] <= trade_to)].copy()
    if df_week.empty:
        return {}

    X = df_week[features].values.astype(np.float32)
    probs = model.predict_proba(X)
    classes = list(model.classes_)

    df_week["signal_score"] = probs[:, classes.index(2)] - probs[:, classes.index(0)]
    df_week["direction"] = df_week["signal_score"].apply(lambda s: "BUY" if s > 0 else "SHORT")

    by_date = defaultdict(list)
    for _, row in df_week.iterrows():
        by_date[row["_date"]].append({
            "slug": row["slug"],
            "signal_score": float(row["signal_score"]),
            "direction": row["direction"],
        })
    for d in by_date:
        by_date[d].sort(key=lambda x: x["signal_score"], reverse=True)
    return by_date


# ═══════════════════════════════════════════════════════════════════════════════
#  TRADE SIMULATION
# ═══════════════════════════════════════════════════════════════════════════════

def simulate_exit(direction, entry_price, candles):
    """Simulate Trailing-J exit on hourly candles."""
    if not candles or not entry_price or entry_price <= 0:
        return 0.0, "no_data"
    act = J_LONG_ACT if direction == "BUY" else J_SHORT_ACT
    floor = J_LONG_TRAIL if direction == "BUY" else J_SHORT_TRAIL
    activated = False
    peak = 0.0
    last = 0.0
    for i, c in enumerate(candles):
        h, l, cl = float(c["high"]), float(c["low"]), float(c["close"])
        if direction == "BUY":
            hp = (h - entry_price) / entry_price
            lp = (l - entry_price) / entry_price
            cp = (cl - entry_price) / entry_price
        else:
            hp = (entry_price - l) / entry_price
            lp = (entry_price - h) / entry_price
            cp = (entry_price - cl) / entry_price
        peak = max(peak, hp)
        last = cp
        if (i + 1) % 4 == 0 or i == len(candles) - 1:
            if not activated and peak >= act:
                activated = True
            if activated and cp <= floor:
                return cp, "trailing_stop"
            if hp >= CURRENT_TP:
                return CURRENT_TP, "take_profit"
            if lp <= CURRENT_SL:
                return CURRENT_SL, "stop_loss"
    return last, "expiry"


def run_sim(signals, ohlcv, coin_filter):
    """Run portfolio simulation. Returns (final_equity, results_list, daily_equity)."""
    filtered = {}
    for d, sigs in signals.items():
        filtered[d] = [s for s in sigs if s["slug"] in coin_filter]

    dates = sorted(filtered.keys())
    equity = CAPITAL
    active = []
    results = []
    daily_equity = []

    for d in dates:
        still = []
        for pos in active:
            if d >= pos["exit_date"]:
                pnl = pos["pnl_pct"] * pos["size"]
                equity += pnl
                results.append({
                    "slug": pos["slug"], "direction": pos["direction"],
                    "pnl_pct": pos["pnl_pct"], "pnl_usd": pnl,
                    "exit_reason": pos["exit_reason"], "size": pos["size"],
                    "entry_date": str(pos["entry_date"]),
                })
            else:
                still.append(pos)
        active = still

        deployed = sum(p["size"] for p in active)
        avail = equity * DEPLOY_PCT - deployed
        if avail <= 0:
            daily_equity.append({"date": str(d), "equity": round(equity, 2)})
            continue

        sigs = filtered[d]
        long_c = [s for s in sigs if float(s["signal_score"]) > 0][:LONG_N]
        short_c = sorted(
            [s for s in sigs if float(s["signal_score"]) < 0],
            key=lambda x: float(x["signal_score"]))[:SHORT_N]
        all_c = [(c, "BUY") for c in long_c] + [(c, "SHORT") for c in short_c]

        if not all_c:
            daily_equity.append({"date": str(d), "equity": round(equity, 2)})
            continue

        cur_l = sum(1 for p in active if p["direction"] == "BUY")
        cur_s = sum(1 for p in active if p["direction"] == "SHORT")
        active_set = {(p["slug"], p["direction"]) for p in active}
        new = []
        for cand, direction in all_c:
            slug = cand["slug"]
            if (slug, direction) in active_set:
                continue
            if direction == "BUY" and cur_l >= LONG_N:
                continue
            if direction == "SHORT" and cur_s >= SHORT_N:
                continue
            new.append((slug, direction))
            active_set.add((slug, direction))
            if direction == "BUY":
                cur_l += 1
            else:
                cur_s += 1

        if not new:
            daily_equity.append({"date": str(d), "equity": round(equity, 2)})
            continue

        per = avail / len(new)
        for slug, direction in new:
            cc = ohlcv.get(slug, [])
            ep = None
            for c in cc:
                cd = c["timestamp"].date() if hasattr(c["timestamp"], "date") else c["timestamp"]
                if cd >= d:
                    ep = float(c["open"])
                    break
            if not ep or ep <= 0:
                continue
            end = d + timedelta(days=HOLD_DAYS)
            hc = [c for c in cc
                  if d <= (c["timestamp"].date() if hasattr(c["timestamp"], "date") else c["timestamp"]) <= end]
            pnl_pct, reason = simulate_exit(direction, ep, hc)
            active.append({
                "slug": slug, "direction": direction, "size": per,
                "pnl_pct": pnl_pct, "exit_reason": reason,
                "entry_date": d, "exit_date": d + timedelta(days=HOLD_DAYS),
            })

        daily_equity.append({"date": str(d), "equity": round(equity, 2)})

    for pos in active:
        pnl = pos["pnl_pct"] * pos["size"]
        equity += pnl
        results.append({
            "slug": pos["slug"], "direction": pos["direction"],
            "pnl_pct": pos["pnl_pct"], "pnl_usd": pnl,
            "exit_reason": pos["exit_reason"], "size": pos["size"],
            "entry_date": str(pos["entry_date"]),
        })

    if daily_equity:
        daily_equity[-1]["equity"] = round(equity, 2)

    return equity, results, daily_equity


# ═══════════════════════════════════════════════════════════════════════════════
#  METRICS & REPORTING
# ═══════════════════════════════════════════════════════════════════════════════

def compute_metrics(results, equity, trade_start, trade_end):
    n = len(results)
    if n == 0:
        return {}
    wins = sum(1 for r in results if r["pnl_usd"] > 0)
    pnl = sum(r["pnl_usd"] for r in results)
    ret = (equity - CAPITAL) / CAPITAL * 100
    longs = [r for r in results if r["direction"] == "BUY"]
    shorts = [r for r in results if r["direction"] == "SHORT"]
    gp = sum(r["pnl_usd"] for r in results if r["pnl_usd"] > 0)
    gl = abs(sum(r["pnl_usd"] for r in results if r["pnl_usd"] < 0))
    pf = gp / gl if gl > 0 else float("inf")

    rets = [r["pnl_pct"] for r in results]
    std = np.std(rets, ddof=1) if len(rets) > 1 else 1
    days = max((trade_end - trade_start).days, 1)
    tpy = n / (days / 252)
    sharpe = (np.mean(rets) / std) * np.sqrt(tpy) if std > 0 else 0
    down = [r for r in rets if r < 0]
    ds = np.std(down, ddof=1) if len(down) > 1 else 1
    sortino = (np.mean(rets) / ds) * np.sqrt(tpy) if ds > 0 else 0

    running = CAPITAL
    peak_eq = CAPITAL
    max_dd = 0
    dd_series = []
    for r in results:
        running += r["pnl_usd"]
        peak_eq = max(peak_eq, running)
        dd = (peak_eq - running) / peak_eq
        max_dd = max(max_dd, dd)
        dd_series.append(round(dd * 100, 2))
    calmar = (ret * 252 / days) / (max_dd * 100) if max_dd > 0 else float("inf")

    avg_w = np.mean([r["pnl_usd"] for r in results if r["pnl_usd"] > 0]) if wins else 0
    avg_l = np.mean([r["pnl_usd"] for r in results if r["pnl_usd"] < 0]) if n - wins > 0 else 0
    payoff = abs(avg_w / avg_l) if avg_l != 0 else float("inf")

    l_pnl = sum(r["pnl_usd"] for r in longs)
    s_pnl = sum(r["pnl_usd"] for r in shorts)
    l_wr = sum(1 for r in longs if r["pnl_usd"] > 0) / len(longs) * 100 if longs else 0
    s_wr = sum(1 for r in shorts if r["pnl_usd"] > 0) / len(shorts) * 100 if shorts else 0

    reasons = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    for r in results:
        reasons[r["exit_reason"]]["count"] += 1
        reasons[r["exit_reason"]]["pnl"] += r["pnl_usd"]

    return {
        "pnl_usd": round(pnl, 2),
        "return_pct": round(ret, 2),
        "trades": n,
        "unique_coins": len({r["slug"] for r in results}),
        "win_rate": round(wins / n * 100, 1),
        "long_trades": len(longs),
        "short_trades": len(shorts),
        "long_pnl": round(l_pnl, 2),
        "short_pnl": round(s_pnl, 2),
        "long_wr": round(l_wr, 1),
        "short_wr": round(s_wr, 1),
        "profit_factor": round(min(pf, 99.99), 2),
        "payoff_ratio": round(min(payoff, 99.99), 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "calmar": round(min(calmar, 99.99), 2),
        "avg_win_usd": round(float(avg_w), 2),
        "avg_loss_usd": round(float(avg_l), 2),
        "drawdown_series": dd_series,
        "exit_reasons": {k: {"count": v["count"], "pnl": round(v["pnl"], 2)} for k, v in reasons.items()},
    }


def print_metrics(metrics, label):
    m = metrics
    if not m:
        print(f"\n  {label}: NO TRADES")
        return
    print(f"\n{'=' * 90}")
    print(f"  {label}")
    print(f"{'=' * 90}")
    print(f"  {'P&L':.<30} ${m['pnl_usd']:+,.2f} ({m['return_pct']:+.2f}%)")
    print(f"  {'Trades':.<30} {m['trades']}  ({m['unique_coins']} unique coins)")
    print(f"  {'Win Rate':.<30} {m['win_rate']:.1f}%")
    print(f"  {'L/S':.<30} {m['long_trades']}L / {m['short_trades']}S")
    print(f"  {'Long P&L':.<30} ${m['long_pnl']:+,.2f}  WR={m['long_wr']:.0f}%")
    print(f"  {'Short P&L':.<30} ${m['short_pnl']:+,.2f}  WR={m['short_wr']:.0f}%")
    print(f"  {'Profit Factor':.<30} {m['profit_factor']:.2f}")
    print(f"  {'Payoff Ratio':.<30} {m['payoff_ratio']:.2f}")
    print(f"  {'Sharpe (ann)':.<30} {m['sharpe']:.2f}")
    print(f"  {'Sortino (ann)':.<30} {m['sortino']:.2f}")
    print(f"  {'Max Drawdown':.<30} {m['max_drawdown_pct']:.2f}%")
    print(f"  {'Calmar':.<30} {m['calmar']:.2f}")
    print(f"  Exits:")
    for reason, rv in sorted(m["exit_reasons"].items()):
        print(f"    {reason:<20}: {rv['count']:>4}  ${rv['pnl']:+,.2f}")


def coin_breakdown(results):
    coins = defaultdict(lambda: {
        "long_trades": 0, "long_wins": 0, "long_pnl": 0.0,
        "short_trades": 0, "short_wins": 0, "short_pnl": 0.0,
    })
    for r in results:
        s = r["slug"]
        d = r["direction"]
        k = "long" if d == "BUY" else "short"
        coins[s][f"{k}_trades"] += 1
        coins[s][f"{k}_pnl"] += r["pnl_usd"]
        if r["pnl_usd"] > 0:
            coins[s][f"{k}_wins"] += 1

    print(f"\n  {'Coin':<25} {'Cat':<12} {'Total':>6} {'L-Trd':>6} {'L-WR':>6} {'L-PnL':>9} "
          f"{'S-Trd':>6} {'S-WR':>6} {'S-PnL':>9} {'Net':>9}")
    print(f"  {'-'*25} {'-'*12} {'-'*6} {'-'*6} {'-'*6} {'-'*9} {'-'*6} {'-'*6} {'-'*9} {'-'*9}")

    sorted_coins = sorted(coins.items(), key=lambda x: x[1]["long_pnl"] + x[1]["short_pnl"], reverse=True)
    coin_data = []
    for slug, c in sorted_coins:
        cat = COIN_CATEGORIES.get(slug, "Other")
        total = c["long_trades"] + c["short_trades"]
        l_wr = (c["long_wins"] / c["long_trades"] * 100) if c["long_trades"] > 0 else 0
        s_wr = (c["short_wins"] / c["short_trades"] * 100) if c["short_trades"] > 0 else 0
        net = c["long_pnl"] + c["short_pnl"]
        print(f"  {slug:<25} {cat:<12} {total:>6} {c['long_trades']:>6} {l_wr:>5.0f}% {c['long_pnl']:>+8.2f} "
              f"{c['short_trades']:>6} {s_wr:>5.0f}% {c['short_pnl']:>+8.2f} {net:>+8.2f}")
        coin_data.append({
            "slug": slug, "category": cat, "total_trades": total,
            "long_trades": c["long_trades"], "long_pnl": round(c["long_pnl"], 2), "long_wr": round(l_wr, 1),
            "short_trades": c["short_trades"], "short_pnl": round(c["short_pnl"], 2), "short_wr": round(s_wr, 1),
            "net_pnl": round(net, 2),
        })

    cat_stats = defaultdict(lambda: {
        "long_trades": 0, "long_wins": 0, "long_pnl": 0.0,
        "short_trades": 0, "short_wins": 0, "short_pnl": 0.0, "coins": set(),
    })
    for slug, c in coins.items():
        cat = COIN_CATEGORIES.get(slug, "Other")
        for k in ["long_trades", "long_wins", "long_pnl", "short_trades", "short_wins", "short_pnl"]:
            cat_stats[cat][k] += c[k]
        cat_stats[cat]["coins"].add(slug)

    print(f"\n  {'Category':<15} {'Coins':>5} {'L-Trd':>6} {'L-WR':>6} {'L-PnL':>9} "
          f"{'S-Trd':>6} {'S-WR':>6} {'S-PnL':>9} {'Net':>9}")
    print(f"  {'-'*15} {'-'*5} {'-'*6} {'-'*6} {'-'*9} {'-'*6} {'-'*6} {'-'*9} {'-'*9}")

    cat_data = []
    for cat, c in sorted(cat_stats.items(), key=lambda x: x[1]["long_pnl"] + x[1]["short_pnl"], reverse=True):
        l_wr = (c["long_wins"] / c["long_trades"] * 100) if c["long_trades"] > 0 else 0
        s_wr = (c["short_wins"] / c["short_trades"] * 100) if c["short_trades"] > 0 else 0
        net = c["long_pnl"] + c["short_pnl"]
        print(f"  {cat:<15} {len(c['coins']):>5} {c['long_trades']:>6} {l_wr:>5.0f}% {c['long_pnl']:>+8.2f} "
              f"{c['short_trades']:>6} {s_wr:>5.0f}% {c['short_pnl']:>+8.2f} {net:>+8.2f}")
        cat_data.append({
            "category": cat, "coins": len(c["coins"]),
            "long_trades": c["long_trades"], "long_pnl": round(c["long_pnl"], 2),
            "short_trades": c["short_trades"], "short_pnl": round(c["short_pnl"], 2),
            "net_pnl": round(net, 2),
        })

    return coin_data, cat_data


def monthly_breakdown(results):
    MONTH_NAMES = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
                   7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
    months = defaultdict(list)
    for r in results:
        ed = r["entry_date"]
        if isinstance(ed, str):
            ed = date.fromisoformat(ed)
        months[MONTH_NAMES.get(ed.month, str(ed.month))].append(r)

    print(f"\n{'=' * 90}")
    print(f"  MONTHLY BREAKDOWN")
    print(f"{'=' * 90}")

    monthly_data = {}
    for month, trades in sorted(months.items(), key=lambda x: x[1][0]["entry_date"] if x[1] else ""):
        if not trades:
            print(f"  {month}: NO TRADES")
            monthly_data[month] = {"pnl_usd": 0, "trades": 0, "win_rate": 0}
            continue
        mp = sum(r["pnl_usd"] for r in trades)
        mw = sum(1 for r in trades if r["pnl_usd"] > 0)
        mwr = mw / len(trades) * 100
        ml = [r for r in trades if r["direction"] == "BUY"]
        ms = [r for r in trades if r["direction"] == "SHORT"]
        ml_pnl = sum(r["pnl_usd"] for r in ml)
        ms_pnl = sum(r["pnl_usd"] for r in ms)
        print(f"  {month}: {len(trades)} trades, ${mp:+,.2f}, WR {mwr:.0f}%, "
              f"L={len(ml)} (${ml_pnl:+,.2f}) S={len(ms)} (${ms_pnl:+,.2f})")
        monthly_data[month] = {
            "pnl_usd": round(mp, 2), "trades": len(trades), "win_rate": round(mwr, 1),
            "long_trades": len(ml), "long_pnl": round(ml_pnl, 2),
            "short_trades": len(ms), "short_pnl": round(ms_pnl, 2),
        }
    return monthly_data


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Rolling walk-forward backtest")
    parser.add_argument("--start", default="2026-01-05", help="Trade period start (YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-03-31", help="Trade period end (YYYY-MM-DD)")
    parser.add_argument("--label", default="Q1 2026", help="Label for output files and prints")
    parser.add_argument("--train-start", default=None,
                        help="Training window floor (YYYY-MM-DD). Default: earliest ML_LABELS date.")
    parser.add_argument("--train-months", type=int, default=None,
                        help="Rolling window size in months. If set, train_from = train_to - N months (clamped to floor).")
    args = parser.parse_args()

    trade_start = date.fromisoformat(args.start)
    trade_end = date.fromisoformat(args.end)
    label = args.label
    slug_label = label.lower().replace(" ", "-")

    sundays = get_retrain_sundays(trade_start, trade_end)

    print(f"{'#' * 90}")
    print(f"  {label.upper()} ROLLING WALK-FORWARD BACKTEST (CLEAN OUT-OF-SAMPLE)")
    print(f"  {len(sundays)} weekly retrains · {trade_start} → {trade_end}")
    print(f"  25-coin USDC · Trailing-J · $5,000 @ 85% deploy · 15L/15S · 3-day hold")
    print(f"  Model retrained each Sunday — NO look-ahead bias")
    print(f"{'#' * 90}")

    params = dict(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )
    conn_dbcp = psycopg2.connect(dbname="dbcp", **params)
    conn_bt = psycopg2.connect(dbname="cp_backtest", **params)
    conn_h = psycopg2.connect(dbname="cp_backtest_h", **params)

    # ── Resolve training floor ────────────────────────────────────────────
    if args.train_start:
        train_floor = args.train_start
    else:
        cur = conn_dbcp.cursor()
        cur.execute('SELECT MIN(timestamp)::date FROM "ML_LABELS"')
        train_floor = cur.fetchone()[0].isoformat()
        cur.close()
    train_mode = f"rolling {args.train_months}mo" if args.train_months else "expanding"
    print(f"  Training floor: {train_floor} ({train_mode})")

    # ── Phase 1: Pre-load all data ──────────────────────────────────────────
    feat_end = trade_end.isoformat()
    print(f"\n{'─' * 90}")
    print(f"  PHASE 1: DATA LOADING (single pass — {train_floor} → {feat_end})")
    print(f"{'─' * 90}")

    print(f"\n[1/4] Training feature matrix ({train_floor} → {feat_end})...")
    df_all = load_full_features(conn_dbcp, conn_bt, train_floor, feat_end)
    print(f"  Total: {len(df_all):,} rows, {df_all['slug'].nunique()} coins")

    labels_max = df_all["_date"].max()
    print(f"\n[2/4] Inference features for trade period ({trade_start} → {trade_end})...")
    if labels_max < trade_end:
        print(f"  ML_LABELS ends at {labels_max} — loading OHLCV-anchored features for gap")
        df_infer = load_inference_features(conn_dbcp, conn_bt, str(trade_start), feat_end)
    else:
        print(f"  ML_LABELS covers full trade period — reusing training features")
        df_infer = df_all

    print(f"\n[3/4] Hourly OHLCV ({trade_start} → {trade_end} + buffer)...")
    ohlcv = load_ohlcv(conn_h, USDC_COINS, trade_start, trade_end)

    print("\n[4/4] BTC benchmark...")
    btc = load_btc_benchmark(conn_dbcp, trade_start, trade_end)
    btc_start_price = float(btc[0]["close"]) if btc else 0
    btc_end_price = float(btc[-1]["close"]) if btc else 0
    btc_ret = (btc_end_price - btc_start_price) / btc_start_price * 100 if btc_start_price > 0 else 0
    print(f"  BTC {label}: ${btc_start_price:,.0f} → ${btc_end_price:,.0f} ({btc_ret:+.2f}%)")

    # ── Phase 2: Rolling retrains ───────────────────────────────────────────
    print(f"\n{'─' * 90}")
    print(f"  PHASE 2: ROLLING WALK-FORWARD ({len(sundays)} WEEKLY RETRAINS)")
    print(f"{'─' * 90}")

    all_signals = {}
    week_diagnostics = []

    for i, sunday in enumerate(sundays):
        trade_from = sunday + timedelta(days=1)
        trade_to = min(sunday + timedelta(days=7), trade_end)
        split = compute_splits(sunday, train_floor, args.train_months)

        print(f"\n  Week {i+1:>2}/{len(sundays)}: retrain={sunday}  "
              f"trade={trade_from}→{trade_to}")
        print(f"    train={split['train_from']}→{split['train_to']}  "
              f"val={split['val_from']}→{split['val_to']}")

        model, used_features, diag = train_model(df_all, split)
        if model is None:
            print(f"    SKIP: insufficient data")
            continue

        print(f"    Train: {diag['train_rows']:,} rows ({diag['train_coins']} coins) | "
              f"Val: {diag['val_rows']:,} rows | "
              f"IC-3d: {diag['val_ic_3d']:.4f} | Acc: {diag['val_accuracy']:.1%} | "
              f"Iter: {diag['best_iteration']}")

        week_signals = generate_signals(model, used_features, df_all, df_infer, trade_from, trade_to)
        n_sigs = sum(len(v) for v in week_signals.values())
        n_usdc = len([s for v in week_signals.values() for s in v if s["slug"] in USDC_COINS])
        print(f"    Signals: {n_sigs:,} total ({len(week_signals)} days), {n_usdc} USDC-universe")

        all_signals.update(week_signals)
        diag["week"] = i + 1
        diag["retrain_date"] = str(sunday)
        diag["trade_from"] = str(trade_from)
        diag["trade_to"] = str(trade_to)
        diag["n_signals"] = n_sigs
        week_diagnostics.append(diag)

    # ── Phase 3: Simulate ──────────────────────────────────────────────────
    print(f"\n{'─' * 90}")
    print(f"  PHASE 3: PORTFOLIO SIMULATION")
    print(f"{'─' * 90}")

    eq, results, daily_equity = run_sim(all_signals, ohlcv, USDC_COINS)
    print(f"\n  Signal days: {len(all_signals)} | Total trades: {len(results)}")

    metrics = compute_metrics(results, eq, trade_start, trade_end)
    print_metrics(metrics, f"25-coin USDC - Trailing J - {label} (OUT-OF-SAMPLE)")
    coin_data, cat_data = coin_breakdown(results)
    monthly_data = monthly_breakdown(results)

    # Weekly equity
    print(f"\n  WEEKLY EQUITY:")
    weekly_data = []
    for diag in week_diagnostics:
        tf = diag["trade_from"]
        tt = diag["trade_to"]
        week_trades = [r for r in results if tf <= r["entry_date"] <= tt]
        wp = sum(r["pnl_usd"] for r in week_trades)
        wn = len(week_trades)
        wwr = sum(1 for r in week_trades if r["pnl_usd"] > 0) / wn * 100 if wn > 0 else 0
        print(f"    Week {diag['week']:>2} ({tf}→{tt}): {wn:>3} trades, "
              f"${wp:>+8.2f}, WR {wwr:>4.0f}%, Val IC {diag['val_ic_3d']:.4f}")
        weekly_data.append({
            "week": diag["week"],
            "trade_from": tf, "trade_to": tt,
            "trades": wn, "pnl_usd": round(wp, 2), "win_rate": round(wwr, 1),
            "val_ic_3d": diag["val_ic_3d"], "val_accuracy": diag["val_accuracy"],
            "train_rows": diag["train_rows"],
        })

    # Benchmark comparison
    print(f"\n{'=' * 90}")
    print(f"  BENCHMARK COMPARISON")
    print(f"{'=' * 90}")
    ret = metrics.get("return_pct", 0)
    print(f"  Strategy:  {ret:+.2f}% (${metrics.get('pnl_usd', 0):+,.2f})")
    print(f"  BTC B&H:   {btc_ret:+.2f}%")
    print(f"  Alpha:     {ret - btc_ret:+.2f}%")

    # ── Save JSON ──────────────────────────────────────────────────────────
    output = {
        "metadata": {
            "type": "rolling_walk_forward",
            "period": label,
            "trade_dates": f"{trade_start} → {trade_end}",
            "retrains": len(sundays),
            "capital": CAPITAL,
            "deploy_pct": DEPLOY_PCT,
            "universe": "25 USDC coins",
            "exit_strategy": "Trailing-J",
            "hold_days": HOLD_DAYS,
            "long_n": LONG_N, "short_n": SHORT_N,
            "model": "LightGBM (news_augmented)",
            "features": len(ALL_FEATURES),
            "methodology": "Weekly retrain on expanding window, inference on unseen trade week. "
                           "No look-ahead bias. Mirrors live Sunday retrain cycle.",
        },
        "overall": metrics,
        "btc_benchmark": {
            "start_price": round(btc_start_price, 2),
            "end_price": round(btc_end_price, 2),
            "return_pct": round(btc_ret, 2),
        },
        "weekly": weekly_data,
        "monthly": monthly_data,
        "daily_equity": daily_equity,
        "per_coin": coin_data,
        "per_category": cat_data,
        "training_diagnostics": week_diagnostics,
    }

    json_path = os.path.join(ROOT, f"{slug_label}-backtest-results.json")
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  JSON results → {json_path}")

    conn_dbcp.close()
    conn_bt.close()
    conn_h.close()

    print(f"\n{'#' * 90}")
    print(f"  BACKTEST COMPLETE — {label.upper()} OUT-OF-SAMPLE")
    print(f"{'#' * 90}")


if __name__ == "__main__":
    main()
