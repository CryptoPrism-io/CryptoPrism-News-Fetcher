"""
spot_bot.py
Spot trading bot driven by ML ensemble signals.
Reads ML_SIGNALS_V2, buys top-N coins, sells after hold_days.

Usage:
    python -m src.trading.spot_bot --run          # single run: buy signals + close expired
    python -m src.trading.spot_bot --close-all    # emergency: sell everything
    python -m src.trading.spot_bot --status        # show open positions + P&L
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src.db import get_db_conn
from src.trading.spot_exchange import (
    build_exchange, buy_market, sell_market, get_price,
    get_balance, slug_to_symbol, SLUG_TO_SYMBOL,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Config ──
TOP_N = 20                    # Buy top N coins by signal score
TARGET_DEPLOY_PCT = 0.80      # Deploy 80% of total equity
HOLD_DAYS = 3                 # Hold period matching label_3d
MIN_SIGNAL_SCORE = -0.10      # Minimum score (-0.10 = buy relative outperformers)
MAX_OPEN_POSITIONS = 20       # Max concurrent positions
STOP_LOSS_PCT = -0.08         # -8% hard stop


def get_open_positions(conn) -> list[dict]:
    """Fetch all OPEN trades from ML_TRADES."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('SELECT * FROM "ML_TRADES" WHERE status = \'OPEN\' ORDER BY entry_time')
    return cur.fetchall()


def get_latest_signals(conn, n: int = TOP_N) -> list[dict]:
    """Get top-N BUY signals from ML_SIGNALS_V2, filtered to tradeable coins only."""
    tradeable_slugs = list(SLUG_TO_SYMBOL.keys())
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('''
        SELECT slug, signal_score, direction, regime_state, ensemble_confidence
        FROM "ML_SIGNALS_V2"
        WHERE DATE(timestamp) = (SELECT MAX(DATE(timestamp)) FROM "ML_SIGNALS_V2")
          AND signal_score > %s
          AND slug = ANY(%s)
        ORDER BY signal_score DESC
        LIMIT %s
    ''', (MIN_SIGNAL_SCORE, tradeable_slugs, n))
    return cur.fetchall()


def insert_trade(conn, trade: dict):
    """Insert a new trade into ML_TRADES."""
    sql = """
        INSERT INTO "ML_TRADES" (
            slug, symbol, direction, entry_price, quantity, usdt_size,
            signal_score, regime_state, status, entry_time, hold_days, model_id
        ) VALUES (
            %(slug)s, %(symbol)s, %(direction)s, %(entry_price)s, %(quantity)s,
            %(usdt_size)s, %(signal_score)s, %(regime_state)s, 'OPEN',
            %(entry_time)s, %(hold_days)s, %(model_id)s
        )
    """
    with conn.cursor() as cur:
        cur.execute(sql, trade)
    conn.commit()


def close_trade(conn, trade_id: int, exit_price: float, notes: str = ""):
    """Close a trade with exit price and P&L."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('SELECT * FROM "ML_TRADES" WHERE id = %s', (trade_id,))
    trade = cur.fetchone()

    if not trade:
        return

    entry = float(trade["entry_price"])
    qty = float(trade["quantity"])
    pnl_pct = (exit_price - entry) / entry
    pnl_usdt = (exit_price - entry) * qty

    cur.execute('''
        UPDATE "ML_TRADES" SET
            status = 'CLOSED', exit_price = %s, exit_time = %s,
            pnl_usdt = %s, pnl_pct = %s, notes = %s
        WHERE id = %s
    ''', (exit_price, datetime.now(timezone.utc), round(pnl_usdt, 4),
          round(pnl_pct, 6), notes, trade_id))
    conn.commit()

    pnl_emoji = "+" if pnl_pct >= 0 else ""
    log.info(f"  CLOSED {trade['slug']}: {pnl_emoji}{pnl_pct*100:.2f}% (${pnl_usdt:.2f})")


def run_signal_cycle():
    """Main trading cycle: close expired, buy new signals."""
    exchange = build_exchange()
    conn = get_db_conn()

    # 1. Check regime
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('SELECT regime_state, confidence FROM "ML_REGIME" ORDER BY timestamp DESC LIMIT 1')
    regime_row = cur.fetchone()
    regime = regime_row["regime_state"] if regime_row else "choppy"
    regime_conf = float(regime_row["confidence"]) if regime_row else 0.5
    log.info(f"Regime: {regime} (confidence={regime_conf:.2f})")

    if regime == "risk_off":
        log.info("RISK-OFF regime — skipping new entries, checking exits only")

    # 2. Close expired positions (hold_days exceeded)
    open_positions = get_open_positions(conn)
    now = datetime.now(timezone.utc)

    for pos in open_positions:
        entry_time = pos["entry_time"]
        if entry_time and entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)

        days_held = (now - entry_time).days if entry_time else 999
        symbol = pos["symbol"]

        try:
            current_price = get_price(exchange, symbol)
        except Exception as e:
            log.warning(f"  Cannot get price for {symbol}: {e}")
            continue

        entry_price = float(pos["entry_price"])
        pnl_pct = (current_price - entry_price) / entry_price

        # Check stop loss
        if pnl_pct <= STOP_LOSS_PCT:
            log.info(f"  STOP LOSS {pos['slug']}: {pnl_pct*100:.2f}% -> selling")
            try:
                result = sell_market(exchange, symbol, float(pos["quantity"]))
                close_trade(conn, pos["id"], result["price"], "stop_loss")
            except Exception as e:
                log.error(f"  Failed to sell {symbol}: {e}")
            continue

        # Check hold period expiry
        if days_held >= pos["hold_days"]:
            log.info(f"  EXPIRY {pos['slug']}: held {days_held}d ({pnl_pct*100:+.2f}%) -> selling")
            try:
                result = sell_market(exchange, symbol, float(pos["quantity"]))
                close_trade(conn, pos["id"], result["price"], f"expiry_{days_held}d")
            except Exception as e:
                log.error(f"  Failed to sell {symbol}: {e}")
            continue

        log.info(f"  HOLD {pos['slug']}: day {days_held}/{pos['hold_days']}, P&L={pnl_pct*100:+.2f}%")

    # 3. Buy new signals (skip if risk-off)
    if regime == "risk_off":
        conn.close()
        return

    open_positions = get_open_positions(conn)  # refresh after closes
    open_slugs = {p["slug"] for p in open_positions}
    slots_available = MAX_OPEN_POSITIONS - len(open_positions)

    if slots_available <= 0:
        log.info(f"Max positions ({MAX_OPEN_POSITIONS}) reached, no new entries")
        conn.close()
        return

    # Get signals
    signals = get_latest_signals(conn, TOP_N)
    if not signals:
        log.info("No signals available")
        conn.close()
        return

    # Check balance and compute per-trade size
    balance = get_balance(exchange)
    usdt_free = balance["usdt_free"]

    # Calculate how much to deploy: target 80% of total equity
    # Total equity = free USDT + value of open positions
    total_deployed = sum(float(p.get("usdt_size", 0) or 0) for p in open_positions)
    total_equity = usdt_free + total_deployed
    target_deploy = total_equity * TARGET_DEPLOY_PCT
    to_deploy = max(0, target_deploy - total_deployed)
    usdt_per_trade = to_deploy / max(slots_available, 1)
    usdt_per_trade = max(usdt_per_trade, 12)  # minimum $12 per trade (Binance min)

    log.info(f"Equity: ${total_equity:.2f} | Deployed: ${total_deployed:.2f} | "
             f"Target: ${target_deploy:.2f} | Per trade: ${usdt_per_trade:.2f} | "
             f"Slots: {slots_available}")

    bought = 0
    for sig in signals:
        if bought >= slots_available:
            break
        if usdt_free < usdt_per_trade:
            log.info("Insufficient USDT balance")
            break

        slug = sig["slug"]
        if slug in open_slugs:
            continue

        symbol = slug_to_symbol(slug)
        if not symbol:
            continue

        # Verify symbol exists and has reasonable price
        if symbol not in exchange.markets:
            log.info(f"  SKIP {slug}: {symbol} not on exchange")
            continue
        try:
            price = get_price(exchange, symbol)
            if price < 0.01:
                log.info(f"  SKIP {slug}: price {price} too low")
                continue
        except Exception as e:
            log.info(f"  SKIP {slug}: price error {e}")
            continue

        score = float(sig["signal_score"])
        log.info(f"  SIGNAL: {slug} ({symbol}) score={score:+.4f} @ ${price:.4f}")

        try:
            result = buy_market(exchange, symbol, usdt_per_trade)
            insert_trade(conn, {
                "slug": slug,
                "symbol": symbol,
                "direction": "BUY",
                "entry_price": result["price"],
                "quantity": result["qty"],
                "usdt_size": result["cost"],
                "signal_score": score,
                "regime_state": regime,
                "entry_time": datetime.now(timezone.utc),
                "hold_days": HOLD_DAYS,
                "model_id": 17,
            })
            bought += 1
            usdt_free -= result["cost"]
            open_slugs.add(slug)
        except Exception as e:
            log.error(f"  Failed to buy {slug}: {e}")

    log.info(f"Cycle complete: {bought} new positions, {len(open_positions)} held")
    conn.close()


def close_all():
    """Emergency: sell all open positions."""
    exchange = build_exchange()
    conn = get_db_conn()
    positions = get_open_positions(conn)

    log.info(f"CLOSE ALL: {len(positions)} positions")
    for pos in positions:
        symbol = pos["symbol"]
        try:
            current_price = get_price(exchange, symbol)
            result = sell_market(exchange, symbol, float(pos["quantity"]))
            close_trade(conn, pos["id"], result["price"], "emergency_close")
        except Exception as e:
            log.error(f"  Failed to close {symbol}: {e}")

    conn.close()


def show_status():
    """Show current portfolio status."""
    exchange = build_exchange()
    conn = get_db_conn()
    positions = get_open_positions(conn)

    print(f"\n{'='*70}")
    print(f"PORTFOLIO STATUS — {len(positions)} open positions")
    print(f"{'='*70}\n")

    if not positions:
        print("  No open positions.")
    else:
        total_pnl = 0
        print(f"{'Coin':<15} {'Entry':>8} {'Current':>8} {'P&L%':>8} {'P&L$':>8} {'Days':>5} {'Score':>7}")
        print("-" * 65)

        for pos in positions:
            symbol = pos["symbol"]
            try:
                current = get_price(exchange, symbol)
            except:
                current = float(pos["entry_price"])

            entry = float(pos["entry_price"])
            qty = float(pos["quantity"])
            pnl_pct = (current - entry) / entry
            pnl_usd = (current - entry) * qty
            total_pnl += pnl_usd

            entry_time = pos["entry_time"]
            if entry_time and entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=timezone.utc)
            days = (datetime.now(timezone.utc) - entry_time).days if entry_time else 0
            score = float(pos["signal_score"]) if pos["signal_score"] else 0

            color = "+" if pnl_pct >= 0 else ""
            print(f"{pos['slug']:<15} {entry:>8.4f} {current:>8.4f} {color}{pnl_pct*100:>7.2f}% {color}{pnl_usd:>7.2f} {days:>5} {score:>+.4f}")

        print("-" * 65)
        print(f"{'Total P&L':>40} ${total_pnl:>+8.2f}")

    # Balance
    balance = get_balance(exchange)
    print(f"\nUSDT Free: ${balance['usdt_free']:.2f}")

    # Recent closed trades
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('''
        SELECT slug, pnl_pct, pnl_usdt, notes, exit_time
        FROM "ML_TRADES" WHERE status = 'CLOSED'
        ORDER BY exit_time DESC LIMIT 10
    ''')
    closed = cur.fetchall()
    if closed:
        print(f"\nLast 10 Closed Trades:")
        for t in closed:
            pnl = float(t["pnl_pct"] or 0) * 100
            usd = float(t["pnl_usdt"] or 0)
            c = "+" if pnl >= 0 else ""
            print(f"  {t['slug']:<15} {c}{pnl:.2f}% (${c}{usd:.2f}) — {t['notes']}")

    conn.close()
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ML Spot Trading Bot")
    parser.add_argument("--run", action="store_true", help="Run signal cycle")
    parser.add_argument("--close-all", action="store_true", help="Emergency close all")
    parser.add_argument("--status", action="store_true", help="Show portfolio status")
    args = parser.parse_args()

    if args.close_all:
        close_all()
    elif args.status:
        show_status()
    elif args.run:
        run_signal_cycle()
    else:
        parser.print_help()
