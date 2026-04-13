"""
daily_report.py
Generates a one-page daily trading status report and sends to Telegram.
Runs via cron on the VM at 23:00 UTC daily.

Usage:
    python3 -m src.trading.daily_report
"""

import logging
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

BOT_TOKEN = "8731565244:AAHIoSYtij2YdW4Xp2fC8CYUrEAQ6NrITH8"
CHAT_ID = "7265602912"


def generate_report():
    exchange = build_futures_exchange()
    bal = get_futures_balance(exchange)
    conn = get_db_conn()
    cur = conn.cursor()

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    # Open positions with live P&L
    cur.execute("""
        SELECT slug, symbol, direction, entry_price, quantity, usdt_size, entry_time, hold_days
        FROM "ML_TRADES" WHERE status = 'OPEN' ORDER BY direction, slug
    """)
    open_trades = cur.fetchall()

    longs = []
    shorts = []
    total_long_pnl = 0.0
    total_short_pnl = 0.0

    for slug, symbol, direction, entry, qty, size, etime, hold in open_trades:
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
            entry = (slug[:12], direction[0], s + "%.1f%%" % pnl_pct, "$%.1f" % pnl_usd, "%d/%d" % (days, hold))

            if direction == "BUY":
                longs.append(entry)
            else:
                shorts.append(entry)
        except:
            pass

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
    avg_pnl = float(stats[3] or 0) * 100

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
    lines.append("DAILY BOT REPORT %s" % date_str)
    lines.append("")
    lines.append("BALANCE: $%.2f free / $%.2f total" % (bal["usdt_free"], bal["usdt_total"]))
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

    # Top/bottom positions
    all_pos = longs + shorts
    all_pos_sorted = sorted(all_pos, key=lambda x: float(x[2].replace("+", "").replace("%", "")), reverse=True)
    if all_pos_sorted:
        best = all_pos_sorted[0]
        worst = all_pos_sorted[-1]
        lines.append("BEST:  %s (%s) %s" % (best[0], best[1], best[2]))
        lines.append("WORST: %s (%s) %s" % (worst[0], worst[1], worst[2]))
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

    msg = "\n".join(lines)
    log.info(msg)

    # Send to Telegram
    url = "https://api.telegram.org/bot%s/sendMessage" % BOT_TOKEN
    resp = requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": None})
    if resp.json().get("ok"):
        log.info("Report sent to Telegram")
    else:
        log.error("Telegram send failed: %s" % resp.text)


if __name__ == "__main__":
    generate_report()
