import os
import sys
import time
import argparse
from datetime import datetime, timedelta
from dotenv import load_dotenv
from . import db_cc_news
from .fetch_hourly import push_to_database

load_dotenv()

import requests

API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY")
URL = "https://min-api.cryptocompare.com/data/v2/news/"
HEADERS = {"Content-type": "application/json; charset=UTF-8"}

EXCLUDED_SOURCES = [
    'investing_comcryptonews',
    'investing_comcryptoopinionandanalysis'
]


def fetch_news_for_window(start_dt: datetime, end_dt: datetime) -> list:
    """
    Fetch all articles published between start_dt and end_dt.
    Uses lTs pagination to walk backwards through time.
    """
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    print(f"  Fetching: {start_dt.strftime('%Y-%m-%d %H:%M')} → {end_dt.strftime('%Y-%m-%d %H:%M')}")

    params = {
        "lang": "EN",
        "api_key": API_KEY,
        "lTs": end_ts  # Start from end of window, paginate backwards
    }

    all_articles = []
    page = 0
    max_pages = 50  # Up to 2500 articles per window

    while page < max_pages:
        try:
            resp = requests.get(URL, params=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    ⚠️  API error on page {page+1}: {e}")
            time.sleep(5)
            break

        articles = data.get("Data", [])
        if not articles:
            break

        # Filter to window
        in_window = [a for a in articles if start_ts <= a.get("published_on", 0) <= end_ts]
        in_window = [a for a in in_window if a.get("source", "") not in EXCLUDED_SOURCES]

        all_articles.extend(in_window)

        # If oldest article is before window start, stop
        oldest_ts = min(a.get("published_on", 0) for a in articles)
        if oldest_ts < start_ts:
            break

        # Paginate: set lTs to oldest article timestamp
        params["lTs"] = oldest_ts
        page += 1
        time.sleep(0.5)

    print(f"    → {len(all_articles)} articles found")
    return all_articles


def backfill(start_date: datetime, end_date: datetime, window_hours: int = 24):
    """
    Backfill news from start_date to end_date in windows of window_hours.
    """
    print(f"\n{'='*60}")
    print(f"BACKFILL: {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}")
    print(f"Window size: {window_hours}h | Total: {(end_date - start_date).days} days")
    print(f"{'='*60}\n")

    # Ensure table exists
    db_cc_news.create_cc_news_table()

    current = start_date
    total_inserted = 0
    total_duplicates = 0
    window = timedelta(hours=window_hours)

    while current < end_date:
        window_end = min(current + window, end_date)
        articles = fetch_news_for_window(current, window_end)

        if articles:
            stats = db_cc_news.insert_articles(articles)
            total_inserted += stats.get("inserted", 0)
            total_duplicates += stats.get("duplicates", 0)

        current = window_end
        time.sleep(1)  # Be polite to API

    print(f"\n{'='*60}")
    print(f"BACKFILL COMPLETE")
    print(f"  Total inserted: {total_inserted}")
    print(f"  Duplicates skipped: {total_duplicates}")
    print(f"{'='*60}\n")
    return total_inserted


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill CryptoPrism news")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive)")
    parser.add_argument("--window-hours", type=int, default=24, help="Hours per fetch window (default: 24)")
    args = parser.parse_args()

    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end, "%Y-%m-%d") + timedelta(days=1)  # inclusive

    backfill(start_dt, end_dt, args.window_hours)
