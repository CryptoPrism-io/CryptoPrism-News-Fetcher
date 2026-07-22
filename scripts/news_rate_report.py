"""Daily cc_news ingest-rate report.

Answers: how many articles are we fetching, at what hourly rate, and how does
that compare to the legacy CoinDesk baseline. Runs on GitHub Actions (the DB is
reachable there); credentials come from the DB_* secrets.

Optionally posts a short digest to Telegram when TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_ID are set.
"""
import os
import json
import urllib.request

import psycopg2

# cv ingest cut over at this instant; earlier rows are legacy CoinDesk.
CUTOFF = "2026-07-21 20:00+00"


def connect():
    return psycopg2.connect(
        host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        dbname=os.environ["DB_NAME"])


def main():
    conn = connect()
    cur = conn.cursor()

    def q(sql, args=None):
        cur.execute(sql, args) if args else cur.execute(sql)
        return cur.fetchall()

    lines = []
    add = lines.append

    add("=" * 62)
    add("cc_news INGEST RATE REPORT")
    add("=" * 62)

    # --- last 24h ---
    n24, srcs, over, avg_body = q("""
      select count(*), count(distinct source_name),
             count(*) filter (where body_length >= 300),
             coalesce(round(avg(body_length)), 0)
      from cc_news where fetched_at >= now() - interval '24 hours'""")[0]
    pct = (100 * over // n24) if n24 else 0
    add("")
    add("LAST 24 HOURS")
    add("  articles fetched   : %d" % n24)
    add("  distinct sources   : %d" % srcs)
    add("  body >= 300 chars  : %d (%d%%)" % (over, pct))
    add("  avg body length    : %d chars" % avg_body)

    # --- live hourly rate (today, UTC) ---
    r = q("""
      with hourly as (
        select date_trunc('hour', published_on) h, count(distinct url) c
        from cc_news
        where fetched_at >= %s and published_on >= date_trunc('day', now())
        group by 1
      )
      select count(*), coalesce(sum(c),0), coalesce(round(avg(c),1),0),
             coalesce(max(c),0) from hourly""", (CUTOFF,))[0]
    hrs, tot, avg_hr, mx = r
    add("")
    add("LIVE RATE (today UTC, cv feed)")
    add("  hours covered      : %s" % hrs)
    add("  articles published : %s" % tot)
    add("  AVG per hour       : %s   (max %s)" % (avg_hr, mx))

    # --- baseline comparison ---
    base_all, base_last30 = None, None
    base_all = q("""
      with hourly as (
        select date_trunc('hour', published_on) h, count(distinct url) c
        from cc_news where fetched_at < %s group by 1)
      select round(avg(c),1) from hourly""", (CUTOFF,))[0][0]
    base_last30 = q("""
      with hourly as (
        select date_trunc('hour', published_on) h, count(distinct url) c
        from cc_news where fetched_at < %s
          and published_on >= '2026-06-11' and published_on <= '2026-07-11'
        group by 1)
      select round(avg(c),1) from hourly""", (CUTOFF,))[0][0]
    add("")
    add("BASELINE (legacy CoinDesk)")
    add("  lifetime avg/hr    : %s" % base_all)
    add("  final 30d avg/hr   : %s   <- what we replaced" % base_last30)

    # --- per-hour detail today ---
    add("")
    add("PER-HOUR TODAY (UTC)")
    rows = q("""
      select to_char(date_trunc('hour', published_on),'HH24:00'), count(distinct url)
      from cc_news
      where fetched_at >= %s and published_on >= date_trunc('day', now())
      group by 1 order by 1""", (CUTOFF,))
    for h, n in rows:
        add("  %s  %4d  %s" % (h, n, "#" * min(45, n)))

    total, newest = q("select count(*), max(published_on) from cc_news")[0]
    add("")
    add("  cc_news total      : %d" % total)
    add("  newest published   : %s" % newest)
    add("=" * 62)

    cur.close()
    conn.close()

    report = "\n".join(lines)
    print(report)

    # --- optional Telegram digest ---
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tok and chat:
        digest = (
            "\U0001F4F0 cc_news 24h: %d fetched | %d sources | %d%% bodies>=300\n"
            "Live rate: %s/hr (CoinDesk final: %s/hr)\n"
            "Total: %d | newest %s" % (n24, srcs, pct, avg_hr, base_last30, total, newest))
        payload = {"chat_id": chat, "text": digest}
        topic = os.getenv("TELEGRAM_TOPIC_ID")
        if topic:
            payload["message_thread_id"] = int(topic)
        try:
            req = urllib.request.Request(
                "https://api.telegram.org/bot%s/sendMessage" % tok,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15)
            print("\n[telegram] digest sent")
        except Exception as e:
            print("\n[telegram] send failed (non-fatal): %s" % e)


if __name__ == "__main__":
    main()
