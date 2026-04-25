"""
Per-coin P&L analysis for USDC Trailing J and Expanded Trailing J.
Breaks down long/short PnL, win rate per coin and per category.
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

COIN_CATEGORIES = {
    "bitcoin": "L1-Major", "ethereum": "L1-Major", "solana": "L1-Major",
    "xrp": "L1-Major", "bnb": "L1-Major", "cardano": "L1-Major",
    "litecoin": "L1-Major", "bitcoin-cash": "L1-Fork",
    "avalanche-2": "L1-Alt", "sui": "L1-Alt", "near": "L1-Alt",
    "neo": "L1-Alt", "celestia": "L1-Alt", "hedera-hashgraph": "L1-Alt",
    "chainlink": "DeFi/Infra", "uniswap": "DeFi/Infra", "aave": "DeFi/Infra",
    "curve-dao-token": "DeFi/Infra", "filecoin": "DeFi/Infra",
    "arbitrum": "L2", "zcash": "Privacy",
    "ethena": "DeFi/Infra", "worldcoin-wld": "AI/Identity",
    "dogecoin": "Meme", "dogwifcoin": "Meme", "bonk": "Meme",
    "pepe": "Meme", "shiba-inu": "Meme", "ordinals": "Meme/BTC-Eco",
    "polkadot": "L1-Alt", "cosmos": "L1-Alt", "algorand": "L1-Alt",
    "toncoin": "L1-Alt", "aptos": "L1-Alt", "internet-computer": "L1-Alt",
    "stellar": "L1-Alt", "tron": "L1-Alt", "the-graph": "DeFi/Infra",
    "render-token": "AI/Identity", "fetch-ai": "AI/Identity",
    "injective-protocol": "DeFi/Infra", "sei-network": "L1-Alt",
    "stacks": "L2", "optimism": "L2", "polygon-ecosystem-token": "L2",
    "mantle": "L2", "immutable-x": "L2/Gaming",
    "axie-infinity": "Gaming", "gala": "Gaming", "sandbox": "Gaming",
    "decentraland": "Gaming", "enjincoin": "Gaming",
    "floki": "Meme", "brett": "Meme", "turbo": "Meme",
    "maker": "DeFi/Infra", "lido-dao": "DeFi/Infra", "pendle": "DeFi/Infra",
    "jupiter-exchange-solana": "DeFi/Infra", "raydium": "DeFi/Infra",
    "theta-token": "AI/Identity", "akash-network": "AI/Identity",
    "arweave": "DeFi/Infra", "ontology": "L1-Alt",
    "eos": "L1-Alt", "iota": "L1-Alt", "zilliqa": "L1-Alt",
    "fantom": "L1-Alt", "kaspa": "L1-Alt",
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

    conn.close()
    conn_h.close()
    print(f"  Total load: {time.time()-t0:.1f}s")
    return by_date, ohlcv


def simulate_exit(direction, entry_price, candles):
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


def run_sim(signals, ohlcv):
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
            pnl_pct, reason = simulate_exit(direction, ep, hc)
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


def coin_breakdown(results, label):
    coins = defaultdict(lambda: {
        "long_trades": 0, "long_wins": 0, "long_pnl": 0.0,
        "short_trades": 0, "short_wins": 0, "short_pnl": 0.0,
    })
    for r in results:
        s = r["slug"]
        if r["direction"] == "BUY":
            coins[s]["long_trades"] += 1
            coins[s]["long_pnl"] += r["pnl_usd"]
            if r["pnl_usd"] > 0:
                coins[s]["long_wins"] += 1
        else:
            coins[s]["short_trades"] += 1
            coins[s]["short_pnl"] += r["pnl_usd"]
            if r["pnl_usd"] > 0:
                coins[s]["short_wins"] += 1

    print(f"\n{'=' * 120}")
    print(f"  PER-COIN BREAKDOWN: {label}")
    print(f"{'=' * 120}")
    print(f"  {'Coin':<25} {'Cat':<12} {'Total':>7} {'L-Trd':>6} {'L-WR':>6} {'L-PnL':>9} {'S-Trd':>6} {'S-WR':>6} {'S-PnL':>9} {'Net PnL':>9}")
    print(f"  {'-'*25} {'-'*12} {'-'*7} {'-'*6} {'-'*6} {'-'*9} {'-'*6} {'-'*6} {'-'*9} {'-'*9}")

    sorted_coins = sorted(coins.items(),
                          key=lambda x: x[1]["long_pnl"] + x[1]["short_pnl"], reverse=True)

    for slug, c in sorted_coins:
        cat = COIN_CATEGORIES.get(slug, "Other")
        total = c["long_trades"] + c["short_trades"]
        l_wr = (c["long_wins"] / c["long_trades"] * 100) if c["long_trades"] > 0 else 0
        s_wr = (c["short_wins"] / c["short_trades"] * 100) if c["short_trades"] > 0 else 0
        net = c["long_pnl"] + c["short_pnl"]
        print(f"  {slug:<25} {cat:<12} {total:>7} {c['long_trades']:>6} {l_wr:>5.0f}% {c['long_pnl']:>+8.2f} "
              f"{c['short_trades']:>6} {s_wr:>5.0f}% {c['short_pnl']:>+8.2f} {net:>+8.2f}")

    # Category aggregation
    cat_stats = defaultdict(lambda: {
        "long_trades": 0, "long_wins": 0, "long_pnl": 0.0,
        "short_trades": 0, "short_wins": 0, "short_pnl": 0.0,
        "coins": set(),
    })
    for slug, c in coins.items():
        cat = COIN_CATEGORIES.get(slug, "Other")
        cat_stats[cat]["long_trades"] += c["long_trades"]
        cat_stats[cat]["long_wins"] += c["long_wins"]
        cat_stats[cat]["long_pnl"] += c["long_pnl"]
        cat_stats[cat]["short_trades"] += c["short_trades"]
        cat_stats[cat]["short_wins"] += c["short_wins"]
        cat_stats[cat]["short_pnl"] += c["short_pnl"]
        cat_stats[cat]["coins"].add(slug)

    print(f"\n  {'=' * 110}")
    print(f"  CATEGORY BREAKDOWN: {label}")
    print(f"  {'=' * 110}")
    print(f"  {'Category':<15} {'Coins':>5} {'L-Trd':>6} {'L-WR':>6} {'L-PnL':>9} {'S-Trd':>6} {'S-WR':>6} {'S-PnL':>9} {'Net PnL':>9} {'PnL/Trade':>10}")
    print(f"  {'-'*15} {'-'*5} {'-'*6} {'-'*6} {'-'*9} {'-'*6} {'-'*6} {'-'*9} {'-'*9} {'-'*10}")

    sorted_cats = sorted(cat_stats.items(),
                         key=lambda x: x[1]["long_pnl"] + x[1]["short_pnl"], reverse=True)
    for cat, c in sorted_cats:
        total = c["long_trades"] + c["short_trades"]
        l_wr = (c["long_wins"] / c["long_trades"] * 100) if c["long_trades"] > 0 else 0
        s_wr = (c["short_wins"] / c["short_trades"] * 100) if c["short_trades"] > 0 else 0
        net = c["long_pnl"] + c["short_pnl"]
        ppt = net / total if total > 0 else 0
        print(f"  {cat:<15} {len(c['coins']):>5} {c['long_trades']:>6} {l_wr:>5.0f}% {c['long_pnl']:>+8.2f} "
              f"{c['short_trades']:>6} {s_wr:>5.0f}% {c['short_pnl']:>+8.2f} {net:>+8.2f} {ppt:>+9.2f}")

    return coins


def main():
    # Build expanded universe
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

    print(f"USDC universe: {len(USDC_COINS)} coins")
    print(f"Expanded universe: {len(expanded)} coins")

    # --- USDC Trailing J ---
    print(f"\n{'#' * 120}")
    print(f"  LOADING USDC 29-COIN DATA")
    print(f"{'#' * 120}")
    sigs_usdc, ohlcv_usdc = load_data(USDC_COINS)
    eq_usdc, res_usdc = run_sim(sigs_usdc, ohlcv_usdc)
    total_pnl = sum(r["pnl_usd"] for r in res_usdc)
    print(f"\n  USDC Trailing J: {len(res_usdc)} trades, P&L ${total_pnl:+,.2f} ({(eq_usdc-CAPITAL)/CAPITAL*100:+.2f}%)")
    coin_breakdown(res_usdc, "USDC 29-coin Trailing J")

    # --- Expanded Trailing J ---
    print(f"\n{'#' * 120}")
    print(f"  LOADING EXPANDED {len(expanded)}-COIN DATA")
    print(f"{'#' * 120}")
    sigs_exp, ohlcv_exp = load_data(expanded)
    eq_exp, res_exp = run_sim(sigs_exp, ohlcv_exp)
    total_pnl_exp = sum(r["pnl_usd"] for r in res_exp)
    print(f"\n  Expanded Trailing J: {len(res_exp)} trades, P&L ${total_pnl_exp:+,.2f} ({(eq_exp-CAPITAL)/CAPITAL*100:+.2f}%)")
    coin_breakdown(res_exp, f"Expanded {len(expanded)}-coin Trailing J")

    # --- Top-50 universe by market cap ---
    print(f"\n{'#' * 120}")
    print(f"  TOP-50 COIN UNIVERSE (by avg market cap)")
    print(f"{'#' * 120}")
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"], dbname="dbcp",
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT slug, AVG(market_cap) as avg_mcap
        FROM "1K_coins_ohlcv"
        WHERE timestamp >= '2026-01-01' AND timestamp < '2026-04-01'
          AND market_cap > 0
          AND slug = ANY(%s)
        GROUP BY slug
        ORDER BY avg_mcap DESC
        LIMIT 50
    """, (list(expanded),))
    top50 = {r[0] for r in cur.fetchall()}
    conn.close()
    print(f"  Top 50 coins by avg market cap (from {len(expanded)} with signals+OHLCV)")
    print(f"  Overlap with USDC 29: {len(top50 & USDC_COINS)}/{len(USDC_COINS)}")
    print(f"  New coins: {len(top50 - USDC_COINS)}")
    new_coins = sorted(top50 - USDC_COINS)
    print(f"  Added: {', '.join(new_coins)}")

    sigs_50, ohlcv_50 = load_data(top50)
    eq_50, res_50 = run_sim(sigs_50, ohlcv_50)
    total_pnl_50 = sum(r["pnl_usd"] for r in res_50)
    print(f"\n  Top-50 Trailing J: {len(res_50)} trades, P&L ${total_pnl_50:+,.2f} ({(eq_50-CAPITAL)/CAPITAL*100:+.2f}%)")
    coin_breakdown(res_50, "Top-50 Market Cap Trailing J")

    # --- Final comparison ---
    print(f"\n{'#' * 120}")
    print(f"  FINAL COMPARISON")
    print(f"{'#' * 120}")
    scenarios = [
        ("USDC 29-coin", eq_usdc, res_usdc),
        ("Top-50 Mcap", eq_50, res_50),
        (f"Expanded {len(expanded)}", eq_exp, res_exp),
    ]
    print(f"  {'Scenario':<25} {'P&L':>9} {'Return':>8} {'Trades':>7} {'Coins':>6} {'WR':>6} {'PF':>6} {'MaxDD':>7}")
    print(f"  {'-'*25} {'-'*9} {'-'*8} {'-'*7} {'-'*6} {'-'*6} {'-'*6} {'-'*7}")
    for name, eq, res in scenarios:
        n = len(res)
        pnl = sum(r["pnl_usd"] for r in res)
        ret = (eq - CAPITAL) / CAPITAL * 100
        wins = sum(1 for r in res if r["pnl_usd"] > 0)
        wr = wins / n * 100 if n > 0 else 0
        gp = sum(r["pnl_usd"] for r in res if r["pnl_usd"] > 0)
        gl = abs(sum(r["pnl_usd"] for r in res if r["pnl_usd"] < 0))
        pf = gp / gl if gl > 0 else float("inf")
        running = CAPITAL
        peak_eq = CAPITAL
        max_dd = 0
        for r in res:
            running += r["pnl_usd"]
            peak_eq = max(peak_eq, running)
            max_dd = max(max_dd, (peak_eq - running) / peak_eq)
        unique = len({r["slug"] for r in res})
        print(f"  {name:<25} ${pnl:>+7,.0f} {ret:>+7.2f}% {n:>7} {unique:>6} {wr:>5.1f}% {pf:>5.2f} {max_dd:>6.2%}")


if __name__ == "__main__":
    main()
