"""
spot_bot.py — TRISHULA
Market-neutral trading algorithm: 3-pronged signal (LSTM + TCN + LightGBM).
Longs top-quartile, shorts bottom-quartile on Binance Futures USDC.

    त्रिशूल — Shiva's trident: three views, one strike.

Usage:
    python -m src.trading.spot_bot --run          # single cycle: longs + shorts + close expired
    python -m src.trading.spot_bot --close-all    # emergency: close everything
    python -m src.trading.spot_bot --status        # show portfolio + P&L
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src.db import get_db_conn
from src.trading.futures_exchange import (
    build_futures_exchange, open_long, close_long, open_short, close_short,
    get_futures_price, get_futures_balance, slug_to_futures_symbol,
    SLUG_TO_FUTURES_SYMBOL,
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
LONG_N = 15                   # Top N coins to go long
SHORT_N = 15                  # Bottom N coins to short
TARGET_DEPLOY_PCT = 0.95      # Deploy 95% of account equity per side
HOLD_DAYS = 3                 # Hold period matching label_3d
MIN_SIGNAL_SCORE = -0.15      # Min score for longs (wider net)
MAX_SIGNAL_SCORE = 0.00       # Max score for shorts (anything negative)
MAX_OPEN_POSITIONS = 15       # Max per side (15 long + 15 short = 30 total)
STOP_LOSS_PCT = -0.08         # -8% hard stop per position
TAKE_PROFIT_PCT = 0.045       # +4.5% take-profit per position


def get_open_positions(conn) -> list[dict]:
    """Fetch all OPEN trades from ML_TRADES."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('SELECT * FROM "ML_TRADES" WHERE status = \'OPEN\' ORDER BY entry_time')
    return cur.fetchall()


def sync_db_with_exchange(conn, exchange) -> int:
    """
    Close any DB positions that no longer exist on the exchange.
    Handles testnet resets, manual closes, liquidations.
    Returns number of positions ghost-closed.
    """
    from src.trading.futures_exchange import get_open_futures_positions
    db_positions = get_open_positions(conn)
    if not db_positions:
        return 0

    # Build set of (symbol, side) actually open on exchange
    exchange_positions = get_open_futures_positions(exchange)
    live = {(p["symbol"], p["side"]) for p in exchange_positions}

    ghost_closed = 0
    now = datetime.now(timezone.utc)
    for pos in db_positions:
        symbol = pos["symbol"]
        direction = pos.get("direction", "BUY")
        exchange_side = "SHORT" if direction == "SHORT" else "LONG"
        if (symbol, exchange_side) not in live:
            # Position no longer on exchange — close it in DB at current price
            try:
                exit_price = get_futures_price(exchange, symbol)
            except Exception:
                exit_price = float(pos["entry_price"])
            entry = float(pos["entry_price"])
            qty = float(pos["quantity"])
            if direction == "SHORT":
                pnl_pct = (entry - exit_price) / entry
                pnl_usdt = (entry - exit_price) * qty
            else:
                pnl_pct = (exit_price - entry) / entry
                pnl_usdt = (exit_price - entry) * qty
            with conn.cursor() as cur:
                cur.execute('''
                    UPDATE "ML_TRADES" SET
                        status = 'CLOSED', exit_price = %s, exit_time = %s,
                        pnl_usdt = %s, pnl_pct = %s, notes = 'exchange_reset'
                    WHERE id = %s
                ''', (exit_price, now, round(pnl_usdt, 4), round(pnl_pct, 6), pos["id"]))
            conn.commit()
            log.warning(f"  GHOST-CLOSED {pos['slug']} ({direction}): not on exchange, pnl={pnl_pct*100:+.2f}%")
            ghost_closed += 1

    if ghost_closed:
        log.warning(f"Synced {ghost_closed} ghost position(s) from DB (exchange reset or external close)")
    return ghost_closed


def get_long_signals(conn, n: int = LONG_N) -> list[dict]:
    """Get top-N coins by signal score for LONG (futures). Relative outperformers."""
    tradeable_slugs = list(SLUG_TO_FUTURES_SYMBOL.keys())
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


def get_short_signals(conn, n: int = SHORT_N) -> list[dict]:
    """Get bottom-N coins by signal score for SHORT (futures). Relative underperformers."""
    tradeable_slugs = list(SLUG_TO_FUTURES_SYMBOL.keys())
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('''
        SELECT slug, signal_score, direction, regime_state, ensemble_confidence
        FROM "ML_SIGNALS_V2"
        WHERE DATE(timestamp) = (SELECT MAX(DATE(timestamp)) FROM "ML_SIGNALS_V2")
          AND signal_score < %s
          AND slug = ANY(%s)
        ORDER BY signal_score ASC
        LIMIT %s
    ''', (MAX_SIGNAL_SCORE, tradeable_slugs, n))
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
    direction = trade.get("direction", "BUY")
    if direction == "SHORT":
        pnl_pct = (entry - exit_price) / entry  # short profits when price drops
        pnl_usdt = (entry - exit_price) * qty
    else:
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
    """Main trading cycle: close expired, open longs + shorts (both on futures USDC)."""
    from src.models.registry import get_active_model

    exchange = build_futures_exchange()
    conn = get_db_conn()

    # Step 0: Sync DB with exchange (handles resets, external closes, liquidations)
    sync_db_with_exchange(conn, exchange)

    # Get active model dynamically
    active = get_active_model(conn)
    model_id = active["model_id"] if active else 17
    log.info(f"Active model: id={model_id}")

    bal = get_futures_balance(exchange)
    log.info(f"USDC balance: ${bal['usdt_free']:.2f} free, ${bal['usdt_total']:.2f} total")

    # 1. Check regime
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('SELECT regime_state, confidence FROM "ML_REGIME" ORDER BY timestamp DESC LIMIT 1')
    regime_row = cur.fetchone()
    regime = regime_row["regime_state"] if regime_row else "choppy"
    regime_conf = float(regime_row["confidence"]) if regime_row else 0.5
    log.info(f"Regime: {regime} (confidence={regime_conf:.2f})")

    if regime == "risk_off":
        log.info("RISK-OFF regime — skipping new entries, checking exits only")

    # 2. Close expired positions
    open_positions = get_open_positions(conn)
    now = datetime.now(timezone.utc)

    for pos in open_positions:
        entry_time = pos["entry_time"]
        if entry_time and entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)

        days_held = (now - entry_time).days if entry_time else 999
        symbol = pos["symbol"]
        direction = pos.get("direction", "BUY")
        is_short = direction == "SHORT"

        # Get price from futures exchange
        try:
            current_price = get_futures_price(exchange, symbol)
        except Exception as e:
            log.warning(f"  Cannot get price for {symbol}: {e}")
            continue

        entry_price = float(pos["entry_price"])
        if is_short:
            pnl_pct = (entry_price - current_price) / entry_price
        else:
            pnl_pct = (current_price - entry_price) / entry_price

        # Check stop loss
        if pnl_pct <= STOP_LOSS_PCT:
            log.info(f"  STOP LOSS {pos['slug']} ({direction}): {pnl_pct*100:.2f}%")
            try:
                if is_short:
                    result = close_short(exchange, symbol, float(pos["quantity"]))
                else:
                    result = close_long(exchange, symbol, float(pos["quantity"]))
                close_trade(conn, pos["id"], result["price"], "stop_loss")
            except Exception as e:
                log.error(f"  Failed to close {symbol}: {e}")
            continue

        # Check take-profit
        if pnl_pct >= TAKE_PROFIT_PCT:
            log.info(f"  TAKE PROFIT {pos['slug']} ({direction}): +{pnl_pct*100:.2f}%")
            try:
                if is_short:
                    result = close_short(exchange, symbol, float(pos["quantity"]))
                else:
                    result = close_long(exchange, symbol, float(pos["quantity"]))
                close_trade(conn, pos["id"], result["price"], "take_profit")
            except Exception as e:
                log.error(f"  Failed to close {symbol}: {e}")
            continue

        # Check hold period expiry
        if days_held >= pos["hold_days"]:
            log.info(f"  EXPIRY {pos['slug']} ({direction}): held {days_held}d ({pnl_pct*100:+.2f}%)")
            try:
                if is_short:
                    result = close_short(exchange, symbol, float(pos["quantity"]))
                else:
                    result = close_long(exchange, symbol, float(pos["quantity"]))
                close_trade(conn, pos["id"], result["price"], f"expiry_{days_held}d")
            except Exception as e:
                log.error(f"  Failed to close {symbol}: {e}")
            continue

        tag = "SHORT" if is_short else "LONG"
        log.info(f"  HOLD {pos['slug']} ({tag}): day {days_held}/{pos['hold_days']}, P&L={pnl_pct*100:+.2f}%")

    # 3. Open new positions (skip if risk-off)
    if regime == "risk_off":
        conn.close()
        return

    open_positions = get_open_positions(conn)
    open_slugs = {p["slug"]: p.get("direction", "BUY") for p in open_positions}
    long_count = sum(1 for d in open_slugs.values() if d == "BUY")
    short_count = sum(1 for d in open_slugs.values() if d == "SHORT")

    # ── LONG LEG (futures) ──
    long_slots = MAX_OPEN_POSITIONS - long_count
    if long_slots > 0:
        long_signals = get_long_signals(conn, LONG_N)
        usdc_bal = get_futures_balance(exchange)
        usdc_free = usdc_bal["usdt_free"]
        total_deployed = sum(float(p.get("usdt_size", 0) or 0) for p in open_positions)
        total_equity = usdc_free + total_deployed
        long_deployed = sum(float(p.get("usdt_size", 0) or 0) for p in open_positions if p.get("direction") == "BUY")
        # Each side gets half the equity
        long_target = (total_equity / 2) * TARGET_DEPLOY_PCT
        to_deploy = max(0, long_target - long_deployed)
        per_trade = max(to_deploy / max(long_slots, 1), 12)

        log.info(f"LONGS: equity=${total_equity/2:.0f} deployed=${long_deployed:.0f} per_trade=${per_trade:.0f} slots={long_slots}")

        bought = 0
        for sig in long_signals:
            if bought >= long_slots or usdc_free < per_trade:
                break
            slug = sig["slug"]
            if slug in open_slugs:
                continue
            symbol = slug_to_futures_symbol(slug)
            if not symbol or symbol not in exchange.markets:
                continue
            try:
                price = get_futures_price(exchange, symbol)
                if price < 0.01:
                    continue
            except:
                continue

            score = float(sig["signal_score"])
            log.info(f"  LONG: {slug} ({symbol}) score={score:+.4f} @ ${price:.4f}")
            try:
                result = open_long(exchange, symbol, per_trade)
                insert_trade(conn, {
                    "slug": slug, "symbol": symbol, "direction": "BUY",
                    "entry_price": result["price"], "quantity": result["qty"],
                    "usdt_size": result["cost"], "signal_score": score,
                    "regime_state": regime, "entry_time": datetime.now(timezone.utc),
                    "hold_days": HOLD_DAYS, "model_id": model_id,
                })
                bought += 1
                usdc_free -= result["cost"]
                open_slugs[slug] = "BUY"
            except Exception as e:
                log.error(f"  Failed to buy {slug}: {e}")
        log.info(f"  Opened {bought} longs")
    else:
        log.info(f"LONGS: max positions ({MAX_OPEN_POSITIONS}) reached")

    # ── SHORT LEG (futures) ──
    short_slots = MAX_OPEN_POSITIONS - short_count
    if short_slots > 0:
        short_signals = get_short_signals(conn, SHORT_N)
        usdc_bal = get_futures_balance(exchange)
        usdc_free = usdc_bal["usdt_free"]
        short_deployed = sum(float(p.get("usdt_size", 0) or 0) for p in open_positions if p.get("direction") == "SHORT")
        short_target = (total_equity / 2) * TARGET_DEPLOY_PCT
        to_deploy = max(0, short_target - short_deployed)
        per_trade = max(to_deploy / max(short_slots, 1), 12)

        log.info(f"SHORTS: equity=${total_equity/2:.0f} deployed=${short_deployed:.0f} per_trade=${per_trade:.0f} slots={short_slots}")

        shorted = 0
        for sig in short_signals:
            if shorted >= short_slots or usdc_free < per_trade:
                break
            slug = sig["slug"]
            if slug in open_slugs:
                continue
            fut_symbol = slug_to_futures_symbol(slug)
            if not fut_symbol or fut_symbol not in exchange.markets:
                continue
            try:
                price = get_futures_price(exchange, fut_symbol)
                if price < 0.01:
                    continue
            except:
                continue

            score = float(sig["signal_score"])
            log.info(f"  SHORT: {slug} ({fut_symbol}) score={score:+.4f} @ ${price:.4f}")
            try:
                result = open_short(exchange, fut_symbol, per_trade)
                insert_trade(conn, {
                    "slug": slug, "symbol": fut_symbol, "direction": "SHORT",
                    "entry_price": result["price"], "quantity": result["qty"],
                    "usdt_size": result["cost"], "signal_score": score,
                    "regime_state": regime, "entry_time": datetime.now(timezone.utc),
                    "hold_days": HOLD_DAYS, "model_id": model_id,
                })
                shorted += 1
                usdc_free -= per_trade
                open_slugs[slug] = "SHORT"
            except Exception as e:
                log.error(f"  Failed to short {slug}: {e}")
        log.info(f"  Opened {shorted} shorts")
    else:
        log.info(f"SHORTS: max positions ({MAX_OPEN_POSITIONS}) reached")

    open_positions = get_open_positions(conn)
    longs = sum(1 for p in open_positions if p.get("direction") == "BUY")
    shorts = sum(1 for p in open_positions if p.get("direction") == "SHORT")
    log.info(f"Portfolio: {longs} longs + {shorts} shorts = {longs + shorts} total")

    # Post cycle summary to Telegram (TRISHULA topic)
    try:
        from src.trading.daily_report import send_telegram
        from datetime import datetime, timezone
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        bal = get_futures_balance(exchange)
        lines = [
            f"*TRISHULA Cycle — {now_str}*",
            f"Regime: {regime} ({regime_conf:.2f})",
            f"Portfolio: {longs}L + {shorts}S open",
            f"Balance: ${bal['usdt_free']:.2f} free / ${bal['usdt_total']:.2f} total",
        ]
        if long_slots > 0:
            lines.append(f"Longs opened this cycle: {bought}")
        if short_slots > 0:
            lines.append(f"Shorts opened this cycle: {shorted}")
        send_telegram("\n".join(lines))
    except Exception as _e:
        log.warning(f"Telegram notify failed: {_e}")

    conn.close()


def close_all():
    """Emergency: close all positions (longs + shorts on futures)."""
    exchange = build_futures_exchange()
    conn = get_db_conn()
    positions = get_open_positions(conn)

    log.info(f"CLOSE ALL: {len(positions)} positions")
    for pos in positions:
        symbol = pos["symbol"]
        direction = pos.get("direction", "BUY")
        try:
            if direction == "SHORT":
                result = close_short(exchange, symbol, float(pos["quantity"]))
            else:
                result = close_long(exchange, symbol, float(pos["quantity"]))
            close_trade(conn, pos["id"], result["price"], "emergency_close")
        except Exception as e:
            log.error(f"  Failed to close {symbol} ({direction}): {e}")

    conn.close()


def show_status():
    """Show current portfolio status with long/short breakdown."""
    spot_exchange = build_exchange()
    futures_exchange = None
    if os.getenv("BINANCE_FUTURES_API_KEY", "").strip():
        try:
            futures_exchange = build_futures_exchange()
        except:
            pass

    conn = get_db_conn()
    positions = get_open_positions(conn)

    longs = [p for p in positions if p.get("direction") == "BUY"]
    shorts = [p for p in positions if p.get("direction") == "SHORT"]

    print(f"\n{'='*75}")
    print(f"TRISHULA PORTFOLIO — {len(longs)} longs + {len(shorts)} shorts")
    print(f"{'='*75}")

    total_long_pnl = 0
    total_short_pnl = 0

    for label, side_positions, is_short in [("LONGS (Spot)", longs, False), ("SHORTS (Futures)", shorts, True)]:
        if not side_positions:
            print(f"\n  {label}: none")
            continue
        print(f"\n  {label}:")
        print(f"  {'Coin':<15} {'Side':>5} {'Entry':>8} {'Current':>8} {'P&L%':>8} {'P&L$':>8} {'Days':>5}")
        print(f"  {'-'*63}")

        for pos in side_positions:
            symbol = pos["symbol"]
            try:
                if is_short and futures_exchange:
                    current = get_futures_price(futures_exchange, symbol)
                else:
                    current = get_price(spot_exchange, symbol)
            except:
                current = float(pos["entry_price"])

            entry = float(pos["entry_price"])
            qty = float(pos["quantity"])
            if is_short:
                pnl_pct = (entry - current) / entry
                pnl_usd = (entry - current) * qty
                total_short_pnl += pnl_usd
            else:
                pnl_pct = (current - entry) / entry
                pnl_usd = (current - entry) * qty
                total_long_pnl += pnl_usd

            entry_time = pos["entry_time"]
            if entry_time and entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=timezone.utc)
            days = (datetime.now(timezone.utc) - entry_time).days if entry_time else 0

            tag = "SHORT" if is_short else "LONG"
            c = "+" if pnl_pct >= 0 else ""
            print(f"  {pos['slug']:<15} {tag:>5} {entry:>8.4f} {current:>8.4f} {c}{pnl_pct*100:>7.2f}% {c}{pnl_usd:>7.2f} {days:>5}")

    print(f"\n  {'─'*40}")
    print(f"  Long P&L:  ${total_long_pnl:>+8.2f}")
    print(f"  Short P&L: ${total_short_pnl:>+8.2f}")
    print(f"  Net P&L:   ${total_long_pnl + total_short_pnl:>+8.2f}")

    # Balances
    spot_bal = get_balance(spot_exchange)
    print(f"\n  Spot USDT:    ${spot_bal['usdt_free']:.2f}")
    if futures_exchange:
        fut_bal = get_futures_balance(futures_exchange)
        print(f"  Futures USDT: ${fut_bal['usdt_free']:.2f}")

    # Recent closed trades
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('''
        SELECT slug, direction, pnl_pct, pnl_usdt, notes, exit_time
        FROM "ML_TRADES" WHERE status = 'CLOSED'
        ORDER BY exit_time DESC LIMIT 10
    ''')
    closed = cur.fetchall()
    if closed:
        print(f"\n  Last Closed Trades:")
        for t in closed:
            pnl = float(t["pnl_pct"] or 0) * 100
            usd = float(t["pnl_usdt"] or 0)
            d = t.get("direction", "BUY")
            c = "+" if pnl >= 0 else ""
            print(f"    {t['slug']:<15} {d:<5} {c}{pnl:.2f}% (${c}{usd:.2f}) — {t['notes']}")

    conn.close()
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trishula — Market-Neutral Trading Algorithm")
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
