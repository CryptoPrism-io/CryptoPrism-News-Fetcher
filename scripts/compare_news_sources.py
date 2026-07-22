"""Compare a cryptocurrency.cv ingest sample against real cc_news CoinDesk data.

Runs on GitHub Actions (DB reachable there). It:
  1. Creates the isolated eval table cc_news_cv_eval and inserts the cv sample
     produced locally by fetch_ccv.py (data/eval/cv_sample.json).
  2. Pulls the real cc_news CoinDesk data for a target day (default 2026-07-10).
  3. Prints a side-by-side quality comparison on the dimensions the ML
     sentiment/event pipeline cares about.

Zero-touch: only READS cc_news; only WRITES the new cc_news_cv_eval table.
"""
import os
import sys
import json
import argparse
import statistics

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.news_fetcher.fetch_ccv import insert_db  # reuse the exact ingest insert


def conn():
    return psycopg2.connect(
        host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        dbname=os.environ["DB_NAME"],
    )


def stats_from_rows(rows):
    """rows: list of (body_length, source_name, published_on_date)"""
    n = len(rows)
    if not n:
        return {"n": 0}
    bl = [r[0] or 0 for r in rows]
    over = sum(1 for x in bl if x >= 300)
    srcs = {r[1] for r in rows}
    return {
        "n": n,
        "sources": len(srcs),
        "body_over300_pct": round(100 * over / n, 1),
        "body_median": int(statistics.median(bl)),
        "body_mean": int(statistics.mean(bl)),
        "body_max": max(bl),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cv-json", default="data/eval/cv_sample.json")
    ap.add_argument("--day", default="2026-07-10")
    ap.add_argument("--table", default="cc_news_cv_eval")
    args = ap.parse_args()

    # 1. Insert cv sample into the eval table
    with open(args.cv_json, encoding="utf-8") as f:
        cv_rows = json.load(f)
    print(f"Loaded {len(cv_rows)} cv rows from {args.cv_json}")
    insert_db(cv_rows, args.table)

    c = conn()
    cur = c.cursor()
    cur.execute("SELECT current_database();")
    print(f"DB: {cur.fetchone()[0]}\n")

    # 2. cv eval table stats
    cur.execute(f"SELECT body_length, source_name, published_on::date FROM {args.table}")
    cv = stats_from_rows(cur.fetchall())

    # 3. cc_news real CoinDesk data for the target day
    cur.execute(
        "SELECT body_length, source_name, published_on::date "
        "FROM cc_news WHERE published_on::date = %s", (args.day,))
    cd = stats_from_rows(cur.fetchall())

    # also: how far back can cv reach vs cc_news (context)
    cur.execute(f"SELECT MIN(published_on)::date, MAX(published_on)::date FROM {args.table}")
    cv_range = cur.fetchone()

    cur.close(); c.close()

    def row(label, k, fmt="{}"):
        cvv = fmt.format(cv.get(k, "-")) if cv.get("n") else "-"
        cdv = fmt.format(cd.get(k, "-")) if cd.get("n") else "-"
        print(f"  {label:26} | {str(cvv):>18} | {str(cdv):>18}")

    print("=" * 72)
    print(f"NEWS SOURCE COMPARISON  (cc_news day = {args.day})")
    print("=" * 72)
    print(f"  {'metric':26} | {'cryptocurrency.cv':>18} | {'CoinDesk (cc_news)':>18}")
    print("  " + "-" * 68)
    row("articles", "n")
    row("distinct sources", "sources")
    row("body_length >= 300 (%)", "body_over300_pct", "{}%")
    row("body_length median", "body_median")
    row("body_length mean", "body_mean")
    row("body_length max", "body_max")
    print("  " + "-" * 68)
    print(f"  cv sample date range       : {cv_range[0]} .. {cv_range[1]}")
    print(f"  cc_news target day         : {args.day}  (CoinDesk had {cd.get('n','-')} articles)")
    print("=" * 72)
    print("\nNOTE: cv has no historical archive for 2026, so this is a capability")
    print("comparison (cv=today's sample vs CoinDesk=actual 2026-07-10), not the")
    print("same articles. cv cannot reproduce 2026-07-10 news.")


if __name__ == "__main__":
    main()
