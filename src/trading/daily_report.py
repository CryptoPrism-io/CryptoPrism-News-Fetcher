"""
daily_report.py — TRISHULA Telegram Reports
Sends trading updates to the TRISHULA topic in CryptoPrism.io group.

Usage:
    python3 -m src.trading.daily_report                 # full daily report (23:00 UTC)
    python3 -m src.trading.daily_report --hourly        # compact P&L snapshot (every hour)
    python3 -m src.trading.daily_report --cycle         # post-trade cycle report (every 4h)
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from src.db import get_db_conn
from src.trading.futures_exchange import (
    build_futures_exchange, get_futures_price, get_futures_balance,
    SLUG_TO_FUTURES_SYMBOL,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TOPIC_TRISHULA = int(os.getenv("TELEGRAM_TOPIC_ID", "0"))       # trade updates, cycle reports
TOPIC_ARTHASHASTRI = int(os.getenv("TELEGRAM_PNL_TOPIC_ID", "0"))  # P&L ledger


def send_telegram(text: str, topic_id: int = None):
    """Send a message to a topic in CryptoPrism.io group."""
    tid = topic_id or TOPIC_TRISHULA
    url = "https://api.telegram.org/bot%s/sendMessage" % BOT_TOKEN
    resp = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "message_thread_id": tid,
    })
    if resp.json().get("ok"):
        log.info("Sent to Telegram")
    else:
        log.error("Telegram send failed: %s" % resp.text)


def _fetch_open_positions(exchange, cur):
    """Shared: fetch open positions with live P&L."""
    now = datetime.now(timezone.utc)
    cur.execute("""
        SELECT slug, symbol, direction, entry_price, quantity, usdt_size,
               entry_time, hold_days, signal_score
        FROM "ML_TRADES" WHERE status = 'OPEN' ORDER BY direction, slug
    """)
    open_trades = cur.fetchall()

    longs = []
    shorts = []
    total_long_pnl = 0.0
    total_short_pnl = 0.0

    for slug, symbol, direction, entry, qty, size, etime, hold, sig_score in open_trades:
        try:
            price = get_futures_price(exchange, symbol)
            if direction == "SHORT":
                pnl_pct = (entry - price) / entry * 100
                pnl_usd = (entry - price) * qty
                total_short_pnl += pnl_usd
            else:
                pnl_pct = (price - entry) / entry * 100
                pnl_usd = (price - entry) * qty
                total_long_pnl += pnl_usd

            days = (now - etime.replace(tzinfo=timezone.utc)).days if etime else 0
            s = "+" if pnl_pct >= 0 else ""
            row = {
                "slug": slug[:12], "dir": direction[0], "pnl_pct_str": s + "%.1f%%" % pnl_pct,
                "pnl_usd_str": "$%.1f" % pnl_usd, "days_str": "%d/%d" % (days, hold),
                "pnl_pct": pnl_pct, "pnl_usd": pnl_usd, "signal_score": float(sig_score or 0),
                "direction": direction,
            }
            if direction == "BUY":
                longs.append(row)
            else:
                shorts.append(row)
        except:
            pass

    return longs, shorts, total_long_pnl, total_short_pnl


# ── HOURLY: compact P&L snapshot ──

def hourly_pnl():
    """Compact hourly P&L snapshot with all open positions."""
    exchange = build_futures_exchange()
    bal = get_futures_balance(exchange)
    conn = get_db_conn()
    cur = conn.cursor()

    now = datetime.now(timezone.utc)
    time_str = now.strftime("%H:%M UTC")

    longs, shorts, total_long_pnl, total_short_pnl = _fetch_open_positions(exchange, cur)
    conn.close()

    net = total_long_pnl + total_short_pnl
    sn = "+" if net >= 0 else ""

    sl = "+" if total_long_pnl >= 0 else ""
    ss = "+" if total_short_pnl >= 0 else ""

    lines = []
    in_margin = bal["usdt_total"] - bal["usdt_free"]
    lines.append("ARTHASHASTRI %s" % time_str)
    lines.append("Bal: %.0f USDC free | %.0f total | %.0f deployed" % (
        bal["usdt_free"], bal["usdt_total"], in_margin))
    lines.append("")

    # Strategy-wise P&L
    lines.append("STRATEGY P&L:")
    lines.append("  LONG:  %s$%.2f (%d pos)" % (sl, total_long_pnl, len(longs)))
    lines.append("  SHORT: %s$%.2f (%d pos)" % (ss, total_short_pnl, len(shorts)))
    lines.append("  NET:   %s$%.2f" % (sn, net))
    lines.append("")

    # Positions table
    if longs:
        lines.append("LONGS (%d):" % len(longs))
        for r in sorted(longs, key=lambda x: x["pnl_pct"], reverse=True):
            lines.append("  %s %s %s" % (r["slug"], r["pnl_pct_str"], r["pnl_usd_str"]))

    if shorts:
        lines.append("SHORTS (%d):" % len(shorts))
        for r in sorted(shorts, key=lambda x: x["pnl_pct"], reverse=True):
            lines.append("  %s %s %s" % (r["slug"], r["pnl_pct_str"], r["pnl_usd_str"]))

    if not longs and not shorts:
        lines.append("No open positions")

    msg = "\n".join(lines)
    log.info(msg)
    send_telegram(msg, topic_id=TOPIC_ARTHASHASTRI)


# ── CYCLE: post-trade 4-hourly report with what/why/how ──

def cycle_report():
    """Detailed post-cycle report: what happened, why, how."""
    exchange = build_futures_exchange()
    bal = get_futures_balance(exchange)
    conn = get_db_conn()
    cur = conn.cursor()

    now = datetime.now(timezone.utc)
    time_str = now.strftime("%Y-%m-%d %H:%M UTC")

    longs, shorts, total_long_pnl, total_short_pnl = _fetch_open_positions(exchange, cur)
    net = total_long_pnl + total_short_pnl
    sn = "+" if net >= 0 else ""

    # Regime context
    cur.execute('SELECT regime_state, confidence FROM "ML_REGIME" ORDER BY timestamp DESC LIMIT 1')
    regime_row = cur.fetchone()
    regime = regime_row[0] if regime_row else "unknown"
    regime_conf = float(regime_row[1] or 0) if regime_row else 0

    # Trades opened in last 4 hours
    cur.execute("""
        SELECT slug, symbol, direction, entry_price, usdt_size, signal_score, regime_state
        FROM "ML_TRADES"
        WHERE entry_time >= NOW() - INTERVAL '4 hours'
          AND status = 'OPEN'
        ORDER BY direction, signal_score DESC
    """)
    new_trades = cur.fetchall()

    # Trades closed in last 4 hours
    cur.execute("""
        SELECT slug, direction, pnl_pct, pnl_usdt, notes
        FROM "ML_TRADES"
        WHERE exit_time >= NOW() - INTERVAL '4 hours'
          AND status = 'CLOSED'
        ORDER BY exit_time
    """)
    closed_trades = cur.fetchall()

    # Cumulative stats
    cur.execute("""
        SELECT COUNT(*), SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END), SUM(pnl_usdt)
        FROM "ML_TRADES" WHERE status = 'CLOSED'
    """)
    stats = cur.fetchone()
    total_trades = stats[0] or 0
    winners = stats[1] or 0
    total_pnl = float(stats[2] or 0)

    conn.close()

    # Build message
    lines = []
    lines.append("TRISHULA CYCLE REPORT")
    lines.append(time_str)
    lines.append("")

    # Status bar
    in_margin = bal["usdt_total"] - bal["usdt_free"]
    lines.append("BAL: %.0f USDC free | %.0f total | %.0f deployed | REGIME: %s (%.0f%%)" % (
        bal["usdt_free"], bal["usdt_total"], in_margin, regime.upper(), regime_conf * 100))
    lines.append("POSITIONS: %d L + %d S | NET: %s$%.2f" % (len(longs), len(shorts), sn, net))
    lines.append("")

    # WHAT: new entries this cycle
    if new_trades:
        lines.append("OPENED THIS CYCLE: %d" % len(new_trades))
        for slug, symbol, direction, entry_price, size, sig_score, reg in new_trades:
            d = "L" if direction == "BUY" else "S"
            score = float(sig_score or 0)
            lines.append("  %s %s $%.0f score=%+.3f" % (d, slug[:12], float(size or 0), score))
        lines.append("")

    # WHAT: closed this cycle
    if closed_trades:
        cycle_pnl = sum(float(r[3] or 0) for r in closed_trades)
        cp = "+" if cycle_pnl >= 0 else ""
        lines.append("CLOSED THIS CYCLE: %d (%s$%.2f)" % (len(closed_trades), cp, cycle_pnl))
        for slug, direction, pnl_pct, pnl_usd, notes in closed_trades:
            s = "+" if (pnl_pct or 0) >= 0 else ""
            lines.append("  %s %s %s%.1f%% ($%.2f) %s" % (
                slug[:12], direction[0], s, (pnl_pct or 0) * 100, float(pnl_usd or 0), notes or ""))
        lines.append("")

    if not new_trades and not closed_trades:
        lines.append("NO TRADES THIS CYCLE")
        lines.append("")

    # WHY: signal rationale
    lines.append("WHY:")
    if regime == "risk_off":
        lines.append("  Regime is RISK-OFF — no new entries, exits only")
    else:
        lines.append("  Regime: %s — normal operations" % regime)
        if new_trades:
            long_new = [t for t in new_trades if t[2] == "BUY"]
            short_new = [t for t in new_trades if t[2] == "SHORT"]
            if long_new:
                avg_l = sum(float(t[5] or 0) for t in long_new) / len(long_new)
                lines.append("  Long avg signal: %+.3f (top quartile outperformers)" % avg_l)
            if short_new:
                avg_s = sum(float(t[5] or 0) for t in short_new) / len(short_new)
                lines.append("  Short avg signal: %+.3f (bottom quartile underperformers)" % avg_s)
        if closed_trades:
            expired = [t for t in closed_trades if t[4] and "expiry" in t[4]]
            stopped = [t for t in closed_trades if t[4] and "stop" in t[4]]
            if expired:
                lines.append("  %d expired (hold period reached)" % len(expired))
            if stopped:
                lines.append("  %d stopped out (-8%% threshold)" % len(stopped))
    lines.append("")

    # HOW: portfolio composition
    lines.append("HOW (portfolio):")
    all_pos = longs + shorts
    if all_pos:
        best = max(all_pos, key=lambda x: x["pnl_pct"])
        worst = min(all_pos, key=lambda x: x["pnl_pct"])
        lines.append("  Best:  %s (%s) %s" % (best["slug"], best["dir"], best["pnl_pct_str"]))
        lines.append("  Worst: %s (%s) %s" % (worst["slug"], worst["dir"], worst["pnl_pct_str"]))

        sl = "+" if total_long_pnl >= 0 else ""
        ss = "+" if total_short_pnl >= 0 else ""
        lines.append("  Long P&L:  %s$%.2f" % (sl, total_long_pnl))
        lines.append("  Short P&L: %s$%.2f" % (ss, total_short_pnl))
    lines.append("")

    # ALL TIME
    win_rate = (winners / total_trades * 100) if total_trades > 0 else 0
    sp = "+" if total_pnl >= 0 else ""
    lines.append("ALL TIME: %d trades | %.0f%% win | %s$%.2f" % (total_trades, win_rate, sp, total_pnl))

    msg = "\n".join(lines)
    log.info(msg)
    send_telegram(msg)


# ── MONITORING: Phase 1+2 health checks (through May 7, 2026) ──

def _check_monitoring_alerts(conn):
    """Check TP deployment health, short-side P&L, and regime shifts."""
    alerts = []
    try:
        cur = conn.cursor()

        # 1. TP not firing? Count take_profit exits in last 48h
        cur.execute("""
            SELECT COUNT(*) FROM "ML_TRADES"
            WHERE status = 'CLOSED' AND notes = 'take_profit'
              AND exit_time >= NOW() - INTERVAL '48 hours'
        """)
        tp_48h = cur.fetchone()[0]
        if tp_48h == 0:
            alerts.append("!! NO take_profit exits in 48h — TP may not be firing")

        # 2. Short-side weekly realized P&L
        cur.execute("""
            SELECT COALESCE(SUM(pnl_usdt), 0) FROM "ML_TRADES"
            WHERE status = 'CLOSED' AND direction = 'SHORT'
              AND exit_time >= NOW() - INTERVAL '7 days'
              AND notes NOT IN ('account_reset', 'force_closed_migration_to_usdc_futures')
        """)
        short_week_pnl = float(cur.fetchone()[0])
        if short_week_pnl < -50:
            alerts.append("!! Short-side 7d P&L: $%.2f (threshold: -$50)" % short_week_pnl)

        # 3. Regime shift detection
        cur.execute("""
            SELECT regime_state, timestamp FROM "ML_REGIME"
            ORDER BY timestamp DESC LIMIT 2
        """)
        rows = cur.fetchall()
        if len(rows) == 2:
            current, prev = rows[0], rows[1]
            if prev[0] == 'risk_on' and current[0] != 'risk_on':
                alerts.append("!! REGIME SHIFT: %s -> %s — watch short performance" % (prev[0], current[0]))

        # 4. Daily scorecard line (always show)
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE notes = 'take_profit') as tp,
                COUNT(*) FILTER (WHERE notes = 'stop_loss') as sl,
                COUNT(*) FILTER (WHERE notes LIKE 'expiry%%') as exp,
                COALESCE(SUM(pnl_usdt) FILTER (WHERE direction = 'BUY'), 0) as long_pnl,
                COALESCE(SUM(pnl_usdt) FILTER (WHERE direction = 'SHORT'), 0) as short_pnl
            FROM "ML_TRADES"
            WHERE status = 'CLOSED' AND DATE(exit_time) = CURRENT_DATE
              AND notes NOT IN ('account_reset', 'force_closed_migration_to_usdc_futures')
        """)
        r = cur.fetchone()
        alerts.insert(0, "TODAY: TP=%d SL=%d EXP=%d | L=$%.1f S=$%.1f | S_7d=$%.1f" % (
            r[0], r[1], r[2], float(r[3]), float(r[4]), short_week_pnl))

    except Exception as e:
        log.warning("Monitoring alerts failed: %s" % e)

    return alerts


# ── DAILY: full end-of-day report ──

def generate_report():
    """Full daily report at 23:00 UTC."""
    exchange = build_futures_exchange()
    bal = get_futures_balance(exchange)
    conn = get_db_conn()
    cur = conn.cursor()

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    longs, shorts, total_long_pnl, total_short_pnl = _fetch_open_positions(exchange, cur)

    # Today's closed trades
    cur.execute("""
        SELECT slug, direction, pnl_pct, pnl_usdt, notes
        FROM "ML_TRADES"
        WHERE status = 'CLOSED' AND DATE(exit_time) = CURRENT_DATE
        ORDER BY exit_time
    """)
    today_closed = cur.fetchall()

    # Cumulative stats
    cur.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END),
               SUM(pnl_usdt),
               AVG(pnl_pct)
        FROM "ML_TRADES" WHERE status = 'CLOSED'
    """)
    stats = cur.fetchone()
    total_trades = stats[0] or 0
    winners = stats[1] or 0
    total_pnl = float(stats[2] or 0)

    # Regime
    cur.execute('SELECT regime_state, confidence FROM "ML_REGIME" ORDER BY timestamp DESC LIMIT 1')
    regime_row = cur.fetchone()
    regime = regime_row[0] if regime_row else "unknown"
    regime_conf = float(regime_row[1] or 0) if regime_row else 0

    # Last signal run
    cur.execute('SELECT MAX(timestamp) FROM "ML_SIGNALS"')
    last_signal = cur.fetchone()[0]

    conn.close()

    # Build message
    net = total_long_pnl + total_short_pnl
    sn = "+" if net >= 0 else ""

    lines = []
    lines.append("TRISHULA DAILY REPORT %s" % date_str)
    lines.append("")
    in_margin = bal["usdt_total"] - bal["usdt_free"]
    lines.append("BALANCE: %.2f USDC free | %.2f total | %.2f deployed" % (
        bal["usdt_free"], bal["usdt_total"], in_margin))
    lines.append("REGIME: %s (%.0f%%)" % (regime, regime_conf * 100))
    lines.append("POSITIONS: %d long + %d short = %d" % (len(longs), len(shorts), len(longs) + len(shorts)))
    lines.append("")

    # Unrealized P&L
    lines.append("UNREALIZED P&L:")
    sl = "+" if total_long_pnl >= 0 else ""
    ss = "+" if total_short_pnl >= 0 else ""
    lines.append("  Longs:  %s$%.2f" % (sl, total_long_pnl))
    lines.append("  Shorts: %s$%.2f" % (ss, total_short_pnl))
    lines.append("  Net:    %s$%.2f" % (sn, net))
    lines.append("")

    # All positions
    if longs:
        lines.append("LONGS (%d):" % len(longs))
        for r in sorted(longs, key=lambda x: x["pnl_pct"], reverse=True):
            lines.append("  %s %s %s %s" % (r["slug"], r["pnl_pct_str"], r["pnl_usd_str"], r["days_str"]))

    if shorts:
        lines.append("SHORTS (%d):" % len(shorts))
        for r in sorted(shorts, key=lambda x: x["pnl_pct"], reverse=True):
            lines.append("  %s %s %s %s" % (r["slug"], r["pnl_pct_str"], r["pnl_usd_str"], r["days_str"]))
    lines.append("")

    # Best/worst
    all_pos = longs + shorts
    if all_pos:
        best = max(all_pos, key=lambda x: x["pnl_pct"])
        worst = min(all_pos, key=lambda x: x["pnl_pct"])
        lines.append("BEST:  %s (%s) %s" % (best["slug"], best["dir"], best["pnl_pct_str"]))
        lines.append("WORST: %s (%s) %s" % (worst["slug"], worst["dir"], worst["pnl_pct_str"]))
        lines.append("")

    # Today's closes
    if today_closed:
        lines.append("CLOSED TODAY: %d trades" % len(today_closed))
        for slug, direction, pnl_pct, pnl_usd, notes in today_closed:
            s = "+" if (pnl_pct or 0) >= 0 else ""
            lines.append("  %s %s %s%.1f%% ($%.2f) %s" % (
                slug[:12], direction[0], s, (pnl_pct or 0) * 100, pnl_usd or 0, notes or ""))
    else:
        lines.append("CLOSED TODAY: none")
    lines.append("")

    # Cumulative
    win_rate = (winners / total_trades * 100) if total_trades > 0 else 0
    sp = "+" if total_pnl >= 0 else ""
    lines.append("ALL TIME: %d trades, %.0f%% win rate, %s$%.2f P&L" % (total_trades, win_rate, sp, total_pnl))
    lines.append("LAST SIGNAL: %s" % (str(last_signal)[:19] if last_signal else "none"))

    # ── MONITORING ALERTS (Phase 1+2: through May 7) ──
    conn2 = get_db_conn()
    alerts = _check_monitoring_alerts(conn2)
    conn2.close()
    if alerts:
        lines.append("")
        lines.append("ALERTS:")
        for a in alerts:
            lines.append("  %s" % a)

    msg = "\n".join(lines)
    log.info(msg)
    send_telegram(msg)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trishula Telegram Reports")
    parser.add_argument("--hourly", action="store_true", help="Compact hourly P&L snapshot")
    parser.add_argument("--cycle", action="store_true", help="Detailed 4-hourly cycle report")
    args = parser.parse_args()

    if args.hourly:
        hourly_pnl()
    elif args.cycle:
        cycle_report()
    else:
        generate_report()
