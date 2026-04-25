"""
Q1 2026: 29-coin USDC vs full OHLCV universe (proxy for USDT expansion).
Skips live Binance API — uses all coins with both signals + hourly OHLCV.
"""
import psycopg2
import psycopg2.extras
import numpy as np
from dotenv import load_dotenv
from datetime import timedelta
from collections import defaultdict
import os
import sys

sys.stdout.reconfigure(line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, ".env"))

CAPITAL = 5000.0
DEPLOY_PCT = 0.85
CURRENT_SL = -0.08
CURRENT_TP = 0.045
HOLD_DAYS = 3
LONG_N = 15
SHORT_N = 15
MIN_SIGNAL_SCORE = -0.15
MAX_SIGNAL_SCORE = 0.00

J_LONG_ACT = 0.02
J_LONG_TRAIL = -0.015
J_SHORT_ACT = 0.015
J_SHORT_TRAIL = -0.003

USDC_COINS = {
    "bitcoin", "ethereum", "solana", "xrp", "bnb", "dogecoin", "cardano",
    "chainlink", "avalanche-2", "litecoin", "bitcoin-cash", "uniswap",
    "hedera-hashgraph", "sui", "zcash", "aave", "arbitrum", "near",
    "filecoin", "neo", "curve-dao-token", "ethena", "celestia",
    "worldcoin-wld", "dogwifcoin", "bonk", "pepe", "shiba-inu", "ordinals",
}


def load_data(coin_filter):
    import time
    t0 = time.time()
    print(f"  Connecting to DBs...", end=" ")
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"], dbname="dbcp",
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
    )
    conn_h = psycopg2.connect(
        host=os.environ["DB_HOST"], dbname="cp_backtest_h",
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
    )
    print(f"done ({time.time()-t0:.1f}s)")
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur_h = conn_h.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    coins = list(coin_filter)
    print(f"  Querying ML_SIGNALS for {len(coins)} coins...", end=" ")
    t1 = time.time()
    cur.execute("""
        SELECT DATE(timestamp) as d, slug, signal_score, direction
        FROM "ML_SIGNALS"
        WHERE timestamp >= '2026-01-01' AND timestamp < '2026-04-01'
          AND slug = ANY(%s)
        ORDER BY DATE(timestamp), signal_score DESC
    """, (coins,))
    rows = cur.fetchall()
    print(f"{len(rows)} rows ({time.time()-t1:.1f}s)")
    by_date = defaultdict(list)
    for r in rows:
        by_date[r["d"]].append(r)

    print(f"  Querying hourly OHLCV for {len(coins)} coins...", end=" ")
    t2 = time.time()
    cur_h.execute("""
        SELECT slug, timestamp, open, high, low, close
        FROM ohlcv_1h_250_coins
        WHERE timestamp >= '2026-01-01' AND timestamp < '2026-04-05'
          AND slug = ANY(%s)
        ORDER BY slug, timestamp
    """, (coins,))
    ohlcv_rows = cur_h.fetchall()
    print(f"{len(ohlcv_rows)} rows ({time.time()-t2:.1f}s)")
    ohlcv = defaultdict(list)
    for r in ohlcv_rows:
        ohlcv[r["slug"]].append(r)

    print(f"  Querying BTC benchmark...", end=" ")
    t3 = time.time()
    cur.execute("""
        SELECT timestamp::date as d, close FROM "1K_coins_ohlcv"
        WHERE slug='bitcoin' AND timestamp >= '2026-01-01' AND timestamp < '2026-04-01'
        ORDER BY timestamp
    """)
    btc = cur.fetchall()
    print(f"{len(btc)} rows ({time.time()-t3:.1f}s)")

    conn.close()
    conn_h.close()
    print(f"  Total load: {time.time()-t0:.1f}s")
    return by_date, ohlcv, btc


def simulate_exit(direction, entry_price, candles, use_trailing):
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
            if use_trailing:
                if not activated and peak >= act:
                    activated = True
                if activated and cp <= floor:
                    return cp, "trailing_stop"
            if hp >= CURRENT_TP:
                return CURRENT_TP, "take_profit"
            if lp <= CURRENT_SL:
                return CURRENT_SL, "stop_loss"
    return last, "expiry"


def run_sim(signals, ohlcv, use_trailing):
    dates = sorted(signals.keys())
    equity = CAPITAL
    active = []
    results = []
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
                })
            else:
                still.append(pos)
        active = still
        deployed = sum(p["size"] for p in active)
        avail = equity * DEPLOY_PCT - deployed
        if avail <= 0:
            continue
        sigs = signals[d]
        long_c = [s for s in sigs if float(s["signal_score"]) > MIN_SIGNAL_SCORE][:LONG_N]
        short_c = sorted(
            [s for s in sigs if float(s["signal_score"]) < MAX_SIGNAL_SCORE],
            key=lambda x: float(x["signal_score"]))[:SHORT_N]
        all_c = [(c, "BUY") for c in long_c] + [(c, "SHORT") for c in short_c]
        if not all_c:
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
            pnl_pct, reason = simulate_exit(direction, ep, hc, use_trailing)
            active.append({
                "slug": slug, "direction": direction, "size": per,
                "pnl_pct": pnl_pct, "exit_reason": reason,
                "entry_date": d, "exit_date": d + timedelta(days=HOLD_DAYS),
            })
    for pos in active:
        pnl = pos["pnl_pct"] * pos["size"]
        equity += pnl
        results.append({
            "slug": pos["slug"], "direction": pos["direction"],
            "pnl_pct": pos["pnl_pct"], "pnl_usd": pnl,
            "exit_reason": pos["exit_reason"], "size": pos["size"],
        })
    return equity, results


def metrics(results, equity):
    n = len(results)
    if n == 0:
        return None
    wins = sum(1 for r in results if r["pnl_usd"] > 0)
    total_pnl = sum(r["pnl_usd"] for r in results)
    ret = (equity - CAPITAL) / CAPITAL * 100
    longs = [r for r in results if r["direction"] == "BUY"]
    shorts = [r for r in results if r["direction"] == "SHORT"]
    long_pnl = sum(r["pnl_usd"] for r in longs)
    short_pnl = sum(r["pnl_usd"] for r in shorts)
    long_wr = sum(1 for r in longs if r["pnl_usd"] > 0) / len(longs) * 100 if longs else 0
    short_wr = sum(1 for r in shorts if r["pnl_usd"] > 0) / len(shorts) * 100 if shorts else 0
    gp = sum(r["pnl_usd"] for r in results if r["pnl_usd"] > 0)
    gl = abs(sum(r["pnl_usd"] for r in results if r["pnl_usd"] < 0))
    pf = gp / gl if gl > 0 else float("inf")
    avg_w = np.mean([r["pnl_usd"] for r in results if r["pnl_usd"] > 0]) if wins else 0
    avg_l = np.mean([r["pnl_usd"] for r in results if r["pnl_usd"] < 0]) if n - wins > 0 else 0
    payoff = abs(avg_w / avg_l) if avg_l != 0 else float("inf")
    rets = [r["pnl_pct"] for r in results]
    std = np.std(rets, ddof=1) if len(rets) > 1 else 1
    tpy = n / (64 / 252)
    sharpe = (np.mean(rets) / std) * np.sqrt(tpy) if std > 0 else 0
    down = [r for r in rets if r < 0]
    ds = np.std(down, ddof=1) if len(down) > 1 else 1
    sortino = (np.mean(rets) / ds) * np.sqrt(tpy) if ds > 0 else 0
    running = CAPITAL
    peak_eq = CAPITAL
    max_dd = 0
    for r in results:
        running += r["pnl_usd"]
        peak_eq = max(peak_eq, running)
        max_dd = max(max_dd, (peak_eq - running) / peak_eq)
    calmar = (ret * 252 / 64) / (max_dd * 100) if max_dd > 0 else float("inf")
    unique = len({r["slug"] for r in results})
    reasons = defaultdict(lambda: {"n": 0, "pnl": 0})
    for r in results:
        reasons[r["exit_reason"]]["n"] += 1
        reasons[r["exit_reason"]]["pnl"] += r["pnl_usd"]
    coin_pnl = defaultdict(float)
    for r in results:
        coin_pnl[r["slug"]] += r["pnl_usd"]
    top5 = sorted(coin_pnl.items(), key=lambda x: x[1], reverse=True)[:5]
    bot5 = sorted(coin_pnl.items(), key=lambda x: x[1])[:5]
    return {
        "pnl": total_pnl, "ret": ret, "trades": n, "coins": unique,
        "wr": wins / n * 100, "pf": pf, "sharpe": sharpe, "sortino": sortino,
        "calmar": calmar, "max_dd": max_dd, "payoff": payoff,
        "long_pnl": long_pnl, "short_pnl": short_pnl,
        "long_n": len(longs), "short_n": len(shorts),
        "long_wr": long_wr, "short_wr": short_wr,
        "reasons": dict(reasons), "top5": top5, "bot5": bot5,
    }


def print_detail(m, label):
    print(f"\n{'=' * 70}")
    print(f"  {label}")
    print(f"{'=' * 70}")
    print(f"  {'P&L':.<30} ${m['pnl']:+,.2f} ({m['ret']:+.2f}%)")
    print(f"  {'Trades':.<30} {m['trades']}  ({m['coins']} unique coins)")
    print(f"  {'Win Rate':.<30} {m['wr']:.1f}%")
    print(f"  {'L/S':.<30} {m['long_n']}L / {m['short_n']}S")
    print(f"  {'Long P&L':.<30} ${m['long_pnl']:+,.2f}  WR={m['long_wr']:.0f}%")
    print(f"  {'Short P&L':.<30} ${m['short_pnl']:+,.2f}  WR={m['short_wr']:.0f}%")
    print(f"  {'Profit Factor':.<30} {m['pf']:.2f}")
    print(f"  {'Payoff Ratio':.<30} {m['payoff']:.2f}")
    print(f"  {'Sharpe (ann)':.<30} {m['sharpe']:.2f}")
    print(f"  {'Sortino (ann)':.<30} {m['sortino']:.2f}")
    print(f"  {'Max Drawdown':.<30} {m['max_dd']:.2%}")
    print(f"  {'Calmar':.<30} {m['calmar']:.2f}")
    print(f"  Exits:")
    for reason in sorted(m["reasons"].keys()):
        rv = m["reasons"][reason]
        print(f"    {reason:<20}: {rv['n']:>4}  ${rv['pnl']:+,.2f}")
    print(f"  Top 5 coins:")
    for slug, pnl in m["top5"]:
        print(f"    {slug:<25} ${pnl:+,.2f}")
    print(f"  Bottom 5 coins:")
    for slug, pnl in m["bot5"]:
        print(f"    {slug:<25} ${pnl:+,.2f}")


def main():
    # Build expanded universe: all coins with both signals + hourly OHLCV
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"], dbname="dbcp",
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
    )
    conn_h = psycopg2.connect(
        host=os.environ["DB_HOST"], dbname="cp_backtest_h",
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
    )
    cur = conn.cursor()
    cur_h = conn_h.cursor()

    cur.execute("""SELECT DISTINCT slug FROM "ML_SIGNALS"
                   WHERE timestamp >= '2026-01-01' AND timestamp < '2026-04-01'""")
    sig_coins = {r[0] for r in cur.fetchall()}

    cur_h.execute("""SELECT DISTINCT slug FROM ohlcv_1h_250_coins
                     WHERE timestamp >= '2026-01-01'""")
    ohlcv_coins = {r[0] for r in cur_h.fetchall()}

    expanded = sig_coins & ohlcv_coins
    conn.close()
    conn_h.close()

    print(f"Signal coins: {len(sig_coins)}")
    print(f"Hourly OHLCV coins: {len(ohlcv_coins)}")
    print(f"USDC universe: {len(USDC_COINS)}")
    print(f"Expanded universe: {len(expanded)}")
    print(f"New coins from expansion: {len(expanded - USDC_COINS)}")

    # Load USDC data
    print("\nLoading 29-coin USDC data...")
    sigs_usdc, ohlcv_usdc, btc = load_data(USDC_COINS)
    print(f"  {len(sigs_usdc)} signal days")

    # Load expanded data
    print("Loading expanded universe data...")
    sigs_exp, ohlcv_exp, _ = load_data(expanded)
    print(f"  {len(sigs_exp)} signal days")

    btc_start = float(btc[0]["close"])
    btc_end = float(btc[-1]["close"])
    btc_ret = (btc_end - btc_start) / btc_start * 100
    btc_bench = btc_ret * DEPLOY_PCT
    print(f"\nBTC Q1: ${btc_start:,.0f} -> ${btc_end:,.0f} ({btc_ret:+.2f}%)")

    print(f"\n{'#' * 70}")
    print(f"  UNIVERSE EXPANSION BACKTEST - Q1 2026 (BTC {btc_ret:+.1f}%)")
    print(f"  $5,000 @ 85% deploy, 15L/15S, 3-day hold")
    print(f"{'#' * 70}")

    # 4 scenarios
    eq1, r1 = run_sim(sigs_usdc, ohlcv_usdc, False)
    m1 = metrics(r1, eq1)
    print_detail(m1, "29-coin USDC - Current Exits")

    eq2, r2 = run_sim(sigs_usdc, ohlcv_usdc, True)
    m2 = metrics(r2, eq2)
    print_detail(m2, "29-coin USDC - Trailing J")

    eq3, r3 = run_sim(sigs_exp, ohlcv_exp, False)
    m3 = metrics(r3, eq3)
    print_detail(m3, f"{len(expanded)}-coin Expanded - Current Exits")

    eq4, r4 = run_sim(sigs_exp, ohlcv_exp, True)
    m4 = metrics(r4, eq4)
    print_detail(m4, f"{len(expanded)}-coin Expanded - Trailing J")

    # Summary table
    print(f"\n{'=' * 105}")
    print(f"  SUMMARY - Q1 2026 | BTC: {btc_ret:+.1f}% | BTC bench (85%): {btc_bench:+.1f}%")
    print(f"{'=' * 105}")
    hdr = f"  {'Metric':<20} {'USDC Curr':>11} {'USDC Trail':>11} {'Expand Curr':>12} {'Expand Trail':>12}"
    print(hdr)
    print(f"  {'-' * 20} {'-' * 11} {'-' * 11} {'-' * 12} {'-' * 12}")

    def row(label, key, fmt=".2f"):
        vals = [m1, m2, m3, m4]
        parts = []
        for v in vals:
            if v and key in v:
                parts.append(f"{v[key]:{fmt}}")
            else:
                parts.append("---")
        print(f"  {label:<20} {parts[0]:>11} {parts[1]:>11} {parts[2]:>12} {parts[3]:>12}")

    row("P&L ($)", "pnl", "+,.0f")
    row("Return (%)", "ret", "+.2f")
    row("Trades", "trades", ".0f")
    row("Unique Coins", "coins", ".0f")
    row("Win Rate (%)", "wr", ".1f")
    row("Longs", "long_n", ".0f")
    row("Shorts", "short_n", ".0f")
    row("Long P&L ($)", "long_pnl", "+,.0f")
    row("Short P&L ($)", "short_pnl", "+,.0f")
    row("Long WR (%)", "long_wr", ".0f")
    row("Short WR (%)", "short_wr", ".0f")
    row("Profit Factor", "pf", ".2f")
    row("Payoff Ratio", "payoff", ".2f")
    row("Sharpe (ann)", "sharpe", ".2f")
    row("Sortino (ann)", "sortino", ".2f")
    row("Max DD (%)", "max_dd", ".2%")
    row("Calmar", "calmar", ".2f")

    print(f"\n  EDGE FROM EXPANSION:")
    print(f"    Current exits: ${m3['pnl'] - m1['pnl']:+,.2f} P&L, Sharpe {m3['sharpe']:.2f} vs {m1['sharpe']:.2f}")
    print(f"    Trailing J:    ${m4['pnl'] - m2['pnl']:+,.2f} P&L, Sharpe {m4['sharpe']:.2f} vs {m2['sharpe']:.2f}")
    print(f"    Best combo:    {'Expanded + Trailing' if m4['pnl'] > max(m1['pnl'], m2['pnl'], m3['pnl']) else 'See table'}")


if __name__ == "__main__":
    main()
