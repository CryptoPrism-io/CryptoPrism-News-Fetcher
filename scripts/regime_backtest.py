"""
regime_backtest.py
Replay all ML_TRADES through 8 different regime detection methods.
Compare P&L outcomes to find the optimal regime filter for TRISHULA.

Methods tested:
  0. NO_REGIME      — baseline, take all trades
  1. CURRENT_RULES  — existing rule_based_regime (choppy/risk_on/risk_off)
  2. BTC_TREND      — 7d SMA > 20d SMA for longs, inverse for shorts
  3. BTC_MOMENTUM   — 3d BTC return > 0 for longs, < 0 for shorts
  4. VOL_SCALING    — scale position size by inverse volatility
  5. FGI_THRESHOLD  — scale exposure by Fear & Greed Index
  6. COMPOSITE      — momentum + volatility + FGI combined scoring
  7. LONG_ONLY      — disable all shorts

Usage:
    python scripts/regime_backtest.py
"""

import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()


def get_conn():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", 5432),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def load_trades(conn) -> pd.DataFrame:
    """Load all closed trades with meaningful PnL (exclude $0 flat trades)."""
    df = pd.read_sql("""
        SELECT id, slug, direction, symbol, entry_price, exit_price,
               quantity, usdt_size, pnl_usdt, pnl_pct, signal_score,
               regime_state, entry_time, exit_time, hold_days, notes
        FROM "ML_TRADES"
        WHERE status = 'CLOSED'
        ORDER BY entry_time
    """, conn)
    df["entry_date"] = pd.to_datetime(df["entry_time"]).dt.date
    df["exit_date"] = pd.to_datetime(df["exit_time"]).dt.date
    return df


def load_btc_features(conn) -> pd.DataFrame:
    """Load BTC OHLCV and compute regime features."""
    btc = pd.read_sql("""
        SELECT timestamp::date as d, close, volume
        FROM "1K_coins_ohlcv"
        WHERE slug = 'bitcoin' AND timestamp >= '2026-01-01'
        ORDER BY timestamp
    """, conn)
    btc = btc.rename(columns={"d": "date"})
    btc["ret"] = btc["close"].pct_change()

    # Volatility
    btc["vol_7d"] = btc["ret"].rolling(7).std()
    btc["vol_30d"] = btc["ret"].rolling(30).std()
    btc["vol_ratio"] = btc["vol_7d"] / btc["vol_30d"].replace(0, np.nan)

    # Momentum
    btc["mom_1d"] = btc["close"].pct_change(1)
    btc["mom_3d"] = btc["close"].pct_change(3)
    btc["mom_7d"] = btc["close"].pct_change(7)

    # Moving averages
    btc["sma_7"] = btc["close"].rolling(7).mean()
    btc["sma_20"] = btc["close"].rolling(20).mean()
    btc["sma_50"] = btc["close"].rolling(50).mean()

    # Volume features
    btc["vol_ma_7"] = btc["volume"].rolling(7).mean()
    btc["vol_spike"] = btc["volume"] / btc["vol_ma_7"].replace(0, np.nan)

    return btc


def load_fgi(conn) -> pd.DataFrame:
    """Load Fear & Greed Index."""
    fgi = pd.read_sql("""
        SELECT timestamp::date as date, fear_greed_index as fgi
        FROM "FE_FEAR_GREED_CMC"
        WHERE timestamp >= '2026-01-01'
        ORDER BY timestamp
    """, conn)
    return fgi


def load_breadth(conn) -> pd.DataFrame:
    """Compute market breadth: % of top 50 coins above 20d MA."""
    df = pd.read_sql("""
        WITH daily AS (
            SELECT slug, timestamp::date as d, close, market_cap,
                   AVG(close) OVER (PARTITION BY slug ORDER BY timestamp
                                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as ma20
            FROM "1K_coins_ohlcv"
            WHERE timestamp >= '2026-01-01'
        ),
        top50 AS (
            SELECT d, slug, close, ma20,
                   ROW_NUMBER() OVER (PARTITION BY d ORDER BY market_cap DESC NULLS LAST) as rn
            FROM daily WHERE market_cap IS NOT NULL
        )
        SELECT d as date,
               COUNT(*) FILTER (WHERE close > ma20)::float / NULLIF(COUNT(*), 0) as breadth
        FROM top50 WHERE rn <= 50
        GROUP BY d ORDER BY d
    """, conn)
    return df


# ══════════════════════════════════════════════════════════
# REGIME METHODS — each returns (allow_trade: bool, size_mult: float)
# ══════════════════════════════════════════════════════════

def method_no_regime(direction, btc_row, fgi_val, breadth_val):
    """Method 0: No filter — take everything."""
    return True, 1.0


def method_current_rules(direction, btc_row, fgi_val, breadth_val):
    """Method 1: Current rule-based regime (from regime.py)."""
    mom72 = btc_row.get("mom_3d", 0) or 0
    vol_r = btc_row.get("vol_ratio", 1.0) or 1.0
    mom24 = btc_row.get("mom_1d", 0) or 0
    b = breadth_val or 0.5
    fg = fgi_val or 50

    # Breakout
    if vol_r > 2.0 and abs(mom24) > 0.03:
        return True, 1.0
    # Risk-off — skip all entries
    if b < 0.35:
        return False, 0.0
    if mom72 < -0.05 and fg < 30:
        return False, 0.0
    # Risk-on or choppy — trade
    return True, 1.0


def method_btc_trend(direction, btc_row, fgi_val, breadth_val):
    """Method 2: BTC trend-following (7d SMA vs 20d SMA)."""
    sma7 = btc_row.get("sma_7")
    sma20 = btc_row.get("sma_20")
    if sma7 is None or sma20 is None or pd.isna(sma7) or pd.isna(sma20):
        return True, 1.0

    trending_up = sma7 > sma20

    if direction == "BUY":
        return trending_up, 1.0 if trending_up else 0.0
    else:  # SHORT
        return not trending_up, 1.0 if not trending_up else 0.0


def method_btc_momentum(direction, btc_row, fgi_val, breadth_val):
    """Method 3: BTC 3-day momentum gate."""
    mom3d = btc_row.get("mom_3d", 0)
    if mom3d is None or pd.isna(mom3d):
        return True, 1.0

    if direction == "BUY":
        return mom3d > 0, 1.0 if mom3d > 0 else 0.0
    else:  # SHORT
        return mom3d < 0, 1.0 if mom3d < 0 else 0.0


def method_vol_scaling(direction, btc_row, fgi_val, breadth_val):
    """Method 4: Volatility-inverse position sizing."""
    vol7 = btc_row.get("vol_7d")
    vol30 = btc_row.get("vol_30d")
    if vol7 is None or vol30 is None or pd.isna(vol7) or pd.isna(vol30) or vol30 == 0:
        return True, 1.0

    vol_ratio = vol7 / vol30

    # High vol = reduce size, low vol = full size
    if vol_ratio > 2.0:
        return True, 0.3  # 30% size during high vol
    elif vol_ratio > 1.5:
        return True, 0.6  # 60% size
    elif vol_ratio > 1.2:
        return True, 0.8  # 80% size
    else:
        return True, 1.0  # Full size in calm markets


def method_fgi_threshold(direction, btc_row, fgi_val, breadth_val):
    """Method 5: Fear & Greed Index modulation."""
    fg = fgi_val if fgi_val is not None and not pd.isna(fgi_val) else 50

    if direction == "BUY":
        # Long more aggressively in fear (contrarian), less in greed
        if fg < 25:   # Extreme fear → strong buy signal
            return True, 1.3
        elif fg < 40:  # Fear → buy
            return True, 1.1
        elif fg > 75:  # Extreme greed → reduce longs
            return True, 0.5
        elif fg > 60:  # Greed → slightly reduce
            return True, 0.8
        else:
            return True, 1.0
    else:  # SHORT
        # Short more aggressively in greed (contrarian), less in fear
        if fg > 75:   # Extreme greed → strong short signal
            return True, 1.3
        elif fg > 60:  # Greed → short
            return True, 1.1
        elif fg < 25:  # Extreme fear → reduce shorts
            return True, 0.5
        elif fg < 40:  # Fear → slightly reduce shorts
            return True, 0.8
        else:
            return True, 1.0


def method_composite(direction, btc_row, fgi_val, breadth_val):
    """Method 6: Composite adaptive regime — momentum + vol + FGI + breadth."""
    mom3d = btc_row.get("mom_3d", 0) or 0
    mom7d = btc_row.get("mom_7d", 0) or 0
    vol_ratio = btc_row.get("vol_ratio", 1.0) or 1.0
    fg = fgi_val if fgi_val is not None and not pd.isna(fgi_val) else 50
    b = breadth_val if breadth_val is not None and not pd.isna(breadth_val) else 0.5

    # Score components (each -1 to +1)
    # Momentum: positive = bullish
    mom_score = np.clip(mom3d * 20, -1, 1)  # ±5% → ±1

    # Volatility: low vol = calm = good for trend following
    vol_score = np.clip(1.5 - vol_ratio, -1, 1)

    # FGI: contrarian — extreme fear = buy, extreme greed = sell
    fgi_score = np.clip((50 - fg) / 50, -1, 1)

    # Breadth: high breadth = bullish
    breadth_score = np.clip((b - 0.5) * 4, -1, 1)

    # Composite score: weighted average
    composite = (
        0.35 * mom_score +
        0.20 * vol_score +
        0.20 * fgi_score +
        0.25 * breadth_score
    )

    if direction == "BUY":
        if composite > 0.2:
            return True, min(1.0 + composite, 1.5)
        elif composite > -0.1:
            return True, 0.7
        else:
            return False, 0.0
    else:  # SHORT
        if composite < -0.2:
            return True, min(1.0 + abs(composite), 1.5)
        elif composite < 0.1:
            return True, 0.7
        else:
            return False, 0.0


def method_long_only(direction, btc_row, fgi_val, breadth_val):
    """Method 7: Long-only — disable all shorts."""
    if direction == "SHORT":
        return False, 0.0
    return True, 1.0


# Additional methods to test

def method_momentum_asymmetric(direction, btc_row, fgi_val, breadth_val):
    """Method 8: Momentum gate for shorts only — longs always allowed."""
    if direction == "BUY":
        return True, 1.0
    # Shorts only when BTC momentum is negative
    mom3d = btc_row.get("mom_3d", 0) or 0
    mom7d = btc_row.get("mom_7d", 0) or 0
    if mom3d < -0.01 and mom7d < 0:
        return True, 1.0
    return False, 0.0


def method_trend_vol_combo(direction, btc_row, fgi_val, breadth_val):
    """Method 9: BTC trend + volatility scaling combined."""
    sma7 = btc_row.get("sma_7")
    sma20 = btc_row.get("sma_20")
    vol_ratio = btc_row.get("vol_ratio", 1.0) or 1.0

    if sma7 is None or sma20 is None or pd.isna(sma7) or pd.isna(sma20):
        return True, 1.0

    trending_up = sma7 > sma20

    # Vol scaling
    if vol_ratio > 2.0:
        vol_mult = 0.3
    elif vol_ratio > 1.5:
        vol_mult = 0.6
    else:
        vol_mult = 1.0

    if direction == "BUY":
        if trending_up:
            return True, vol_mult
        else:
            return True, vol_mult * 0.5  # Reduce but don't eliminate longs in downtrend
    else:  # SHORT
        if not trending_up:
            return True, vol_mult
        else:
            return False, 0.0  # No shorts in uptrend


def method_signal_confidence(direction, btc_row, fgi_val, breadth_val, signal_score=None):
    """Method 10: Signal score threshold tightening (filter weak signals)."""
    if signal_score is None:
        return True, 1.0
    if direction == "BUY":
        if signal_score > 0.03:
            return True, 1.3
        elif signal_score > 0.0:
            return True, 1.0
        elif signal_score > -0.02:
            return True, 0.7
        else:
            return False, 0.0  # Skip weak long signals
    else:  # SHORT
        if signal_score < -0.05:
            return True, 1.0
        elif signal_score < -0.03:
            return True, 0.7
        else:
            return False, 0.0  # Skip weak short signals


ALL_METHODS = {
    "0_NO_REGIME": method_no_regime,
    "1_CURRENT_RULES": method_current_rules,
    "2_BTC_TREND": method_btc_trend,
    "3_BTC_MOMENTUM": method_btc_momentum,
    "4_VOL_SCALING": method_vol_scaling,
    "5_FGI_THRESHOLD": method_fgi_threshold,
    "6_COMPOSITE": method_composite,
    "7_LONG_ONLY": method_long_only,
    "8_MOM_ASYMMETRIC": method_momentum_asymmetric,
    "9_TREND_VOL": method_trend_vol_combo,
    "10_SIGNAL_CONF": None,  # special handling — needs signal_score
}


def run_backtest():
    conn = get_conn()
    trades = load_trades(conn)
    btc = load_btc_features(conn)
    fgi = load_fgi(conn)
    breadth = load_breadth(conn)
    conn.close()

    # Build lookup dicts keyed by date
    btc_by_date = {}
    for _, row in btc.iterrows():
        btc_by_date[row["date"]] = row.to_dict()

    fgi_by_date = {}
    for _, row in fgi.iterrows():
        fgi_by_date[row["date"]] = row["fgi"]

    breadth_by_date = {}
    for _, row in breadth.iterrows():
        breadth_by_date[row["date"]] = row["breadth"]

    print(f"Loaded: {len(trades)} closed trades, {len(btc)} BTC days, {len(fgi)} FGI days, {len(breadth)} breadth days")
    print(f"Trade date range: {trades['entry_date'].min()} to {trades['entry_date'].max()}")
    print()

    # Filter out flat/noise trades for cleaner comparison
    meaningful_trades = trades[trades["pnl_usdt"].abs() > 0.01].copy()
    all_trades = trades.copy()
    print(f"Meaningful trades (|PnL| > $0.01): {len(meaningful_trades)}")
    print(f"All trades (including flat): {len(all_trades)}")
    print()

    # Run each method on both all trades and meaningful-only
    for trade_set_name, trade_set in [("ALL_TRADES", all_trades), ("MEANINGFUL_ONLY", meaningful_trades)]:
        print(f"\n{'='*100}")
        print(f"  BACKTEST: {trade_set_name} ({len(trade_set)} trades)")
        print(f"{'='*100}")

        results = {}

        for method_name, method_fn in ALL_METHODS.items():
            total_pnl = 0.0
            total_pnl_long = 0.0
            total_pnl_short = 0.0
            trades_taken = 0
            trades_skipped = 0
            longs_taken = 0
            shorts_taken = 0
            wins = 0
            losses = 0
            long_wins = 0
            long_losses = 0
            short_wins = 0
            short_losses = 0
            trade_details = []

            for _, trade in trade_set.iterrows():
                entry_d = trade["entry_date"]
                direction = trade["direction"]
                pnl = float(trade["pnl_usdt"])
                pnl_pct = float(trade["pnl_pct"]) if trade["pnl_pct"] is not None else 0
                size = float(trade["usdt_size"]) if trade["usdt_size"] is not None else 0
                sig_score = float(trade["signal_score"]) if trade["signal_score"] is not None else None

                btc_row = btc_by_date.get(entry_d, {})
                fgi_val = fgi_by_date.get(entry_d)
                breadth_val = breadth_by_date.get(entry_d)

                # Apply regime filter
                if method_name == "10_SIGNAL_CONF":
                    allow, size_mult = method_signal_confidence(
                        direction, btc_row, fgi_val, breadth_val, sig_score
                    )
                else:
                    allow, size_mult = method_fn(direction, btc_row, fgi_val, breadth_val)

                if not allow:
                    trades_skipped += 1
                    continue

                # Scale PnL by size multiplier
                adj_pnl = pnl * size_mult
                total_pnl += adj_pnl
                trades_taken += 1

                if direction == "BUY":
                    longs_taken += 1
                    total_pnl_long += adj_pnl
                    if adj_pnl > 0:
                        long_wins += 1
                    elif adj_pnl < 0:
                        long_losses += 1
                else:
                    shorts_taken += 1
                    total_pnl_short += adj_pnl
                    if adj_pnl > 0:
                        short_wins += 1
                    elif adj_pnl < 0:
                        short_losses += 1

                if adj_pnl > 0:
                    wins += 1
                elif adj_pnl < 0:
                    losses += 1

            win_rate = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
            long_wr = long_wins / (long_wins + long_losses) * 100 if (long_wins + long_losses) > 0 else 0
            short_wr = short_wins / (short_wins + short_losses) * 100 if (short_wins + short_losses) > 0 else 0

            results[method_name] = {
                "total_pnl": total_pnl,
                "long_pnl": total_pnl_long,
                "short_pnl": total_pnl_short,
                "trades_taken": trades_taken,
                "trades_skipped": trades_skipped,
                "longs": longs_taken,
                "shorts": shorts_taken,
                "win_rate": win_rate,
                "long_wr": long_wr,
                "short_wr": short_wr,
                "wins": wins,
                "losses": losses,
            }

        # Print comparison table
        print(f"\n{'Method':<22} {'PnL':>10} {'Long PnL':>10} {'Short PnL':>10} {'Taken':>6} {'Skip':>5} {'L':>4} {'S':>4} {'WR%':>6} {'LWR%':>6} {'SWR%':>6}")
        print("-" * 105)

        # Sort by total PnL descending
        sorted_methods = sorted(results.items(), key=lambda x: x[1]["total_pnl"], reverse=True)

        for name, r in sorted_methods:
            marker = " <<<" if name == sorted_methods[0][0] else ""
            print(
                f"{name:<22} "
                f"{'${:+.2f}'.format(r['total_pnl']):>10} "
                f"{'${:+.2f}'.format(r['long_pnl']):>10} "
                f"{'${:+.2f}'.format(r['short_pnl']):>10} "
                f"{r['trades_taken']:>6} "
                f"{r['trades_skipped']:>5} "
                f"{r['longs']:>4} "
                f"{r['shorts']:>4} "
                f"{r['win_rate']:>5.1f}% "
                f"{r['long_wr']:>5.1f}% "
                f"{r['short_wr']:>5.1f}%"
                f"{marker}"
            )

        # Improvement vs baseline
        baseline = results["0_NO_REGIME"]["total_pnl"]
        print(f"\n  Baseline (no regime): ${baseline:+.2f}")
        print(f"  Improvement vs baseline:")
        for name, r in sorted_methods:
            delta = r["total_pnl"] - baseline
            print(f"    {name:<22} {'${:+.2f}'.format(delta):>10} ({'+' if delta>=0 else ''}{delta/abs(baseline)*100 if baseline != 0 else 0:.1f}%)")

    # Also output as JSON for report generation
    print("\n\nJSON_RESULTS_START")
    json_out = {}
    for name, r in results.items():
        json_out[name] = r
    print(json.dumps(json_out, indent=2))
    print("JSON_RESULTS_END")


if __name__ == "__main__":
    run_backtest()
