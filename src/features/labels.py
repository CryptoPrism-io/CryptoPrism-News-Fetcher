"""
labels.py
Computes forward return labels from 1K_coins_ohlcv (read-only) → ML_LABELS.

Thresholds (tunable):
  label_1d:  ±3%
  label_3d:  ±5%
  label_7d:  ±7%
  label_14d: ±10%

Usage:
    python -m src.features.labels --from-date 2025-10-21  # backfill
    python -m src.features.labels                          # incremental (yesterday only)
"""

import argparse
import logging
import os
from datetime import date, datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Classification thresholds
THRESHOLDS = {
    "label_1d":  0.03,
    "label_3d":  0.05,
    "label_7d":  0.07,
    "label_14d": 0.10,
}


def get_db_conn():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", 5432),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "require"),
    )


def classify(ret: float | None, threshold: float) -> int | None:
    if ret is None:
        return None
    if ret > threshold:
        return 1
    if ret < -threshold:
        return -1
    return 0


def fetch_ohlcv_window(conn, from_date: str, to_date: str) -> list[dict]:
    """
    Fetch daily OHLCV for all coins in the window + 14 extra days forward
    so we can compute forward returns without a second query.
    READ-ONLY on 1K_coins_ohlcv.
    """
    # Need 14 extra days forward for label_14d
    dt_to = datetime.strptime(to_date, "%Y-%m-%d").date() + timedelta(days=18)

    query = """
        SELECT slug, symbol, timestamp, open, high, low, close, volume, market_cap
        FROM "1K_coins_ohlcv"
        WHERE timestamp >= %(from_ts)s
          AND timestamp <= %(to_ts)s
        ORDER BY slug, timestamp ASC
    """
    from_ts = datetime.combine(
        datetime.strptime(from_date, "%Y-%m-%d").date() - timedelta(days=32),
        datetime.min.time()
    ).replace(tzinfo=timezone.utc)

    to_ts = datetime.combine(dt_to, datetime.max.time()).replace(tzinfo=timezone.utc)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, {"from_ts": from_ts, "to_ts": to_ts})
        return cur.fetchall()


def compute_labels(rows: list[dict], from_date: str, to_date: str) -> list[dict]:
    """
    Given sorted per-coin OHLCV rows, compute forward returns and labels
    for each day in [from_date, to_date].
    """
    from collections import defaultdict

    # Group by slug
    by_slug: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_slug[row["slug"]].append(row)

    target_from = datetime.strptime(from_date, "%Y-%m-%d").date()
    target_to   = datetime.strptime(to_date,   "%Y-%m-%d").date()

    label_rows = []

    for slug, candles in by_slug.items():
        # Build date → close map
        date_to_close: dict[date, float] = {}
        date_to_ts: dict[date, datetime] = {}
        for c in candles:
            d = c["timestamp"].date() if hasattr(c["timestamp"], "date") else datetime.fromisoformat(str(c["timestamp"])).date()
            date_to_close[d] = c["close"]
            date_to_ts[d]    = c["timestamp"]

        sorted_dates = sorted(date_to_close.keys())

        # Compute rolling volatility (7d and 30d std of daily returns)
        daily_rets: dict[date, float] = {}
        for i in range(1, len(sorted_dates)):
            d_prev = sorted_dates[i - 1]
            d_curr = sorted_dates[i]
            if date_to_close[d_prev] and date_to_close[d_prev] != 0:
                daily_rets[d_curr] = (date_to_close[d_curr] - date_to_close[d_prev]) / date_to_close[d_prev]

        def rolling_std(d: date, window: int) -> float | None:
            window_dates = [x for x in sorted_dates if x < d][-window:]
            rets = [daily_rets[x] for x in window_dates if x in daily_rets]
            if len(rets) < 3:
                return None
            import statistics
            return statistics.stdev(rets)

        for d in sorted_dates:
            if d < target_from or d > target_to:
                continue

            close_t = date_to_close.get(d)
            if close_t is None or close_t == 0:
                continue

            def fwd_ret(horizon_days: int) -> float | None:
                # Find the closest available date >= d + horizon_days
                target_d = d + timedelta(days=horizon_days)
                # Allow ±1 day tolerance for weekends/gaps
                for delta in range(3):
                    c = date_to_close.get(target_d + timedelta(days=delta))
                    if c and c != 0:
                        return (c - close_t) / close_t
                return None

            r1  = fwd_ret(1)
            r3  = fwd_ret(3)
            r7  = fwd_ret(7)
            r14 = fwd_ret(14)

            ts = date_to_ts[d]

            label_rows.append({
                "slug":            slug,
                "timestamp":       ts,
                "close_price":     close_t,
                "forward_ret_1d":  round(r1,  8) if r1  is not None else None,
                "forward_ret_3d":  round(r3,  8) if r3  is not None else None,
                "forward_ret_7d":  round(r7,  8) if r7  is not None else None,
                "forward_ret_14d": round(r14, 8) if r14 is not None else None,
                "label_1d":  classify(r1,  THRESHOLDS["label_1d"]),
                "label_3d":  classify(r3,  THRESHOLDS["label_3d"]),
                "label_7d":  classify(r7,  THRESHOLDS["label_7d"]),
                "label_14d": classify(r14, THRESHOLDS["label_14d"]),
                "volatility_7d":  rolling_std(d, 7),
                "volatility_30d": rolling_std(d, 30),
                "created_at": datetime.now(timezone.utc),
            })

    return label_rows


def upsert_labels(conn, rows: list[dict]):
    sql = """
        INSERT INTO "ML_LABELS" (
            slug, timestamp, close_price,
            forward_ret_1d, forward_ret_3d, forward_ret_7d, forward_ret_14d,
            label_1d, label_3d, label_7d, label_14d,
            volatility_7d, volatility_30d, created_at
        ) VALUES (
            %(slug)s, %(timestamp)s, %(close_price)s,
            %(forward_ret_1d)s, %(forward_ret_3d)s, %(forward_ret_7d)s, %(forward_ret_14d)s,
            %(label_1d)s, %(label_3d)s, %(label_7d)s, %(label_14d)s,
            %(volatility_7d)s, %(volatility_30d)s, %(created_at)s
        )
        ON CONFLICT (slug, timestamp) DO UPDATE SET
            close_price     = EXCLUDED.close_price,
            forward_ret_1d  = EXCLUDED.forward_ret_1d,
            forward_ret_3d  = EXCLUDED.forward_ret_3d,
            forward_ret_7d  = EXCLUDED.forward_ret_7d,
            forward_ret_14d = EXCLUDED.forward_ret_14d,
            label_1d        = EXCLUDED.label_1d,
            label_3d        = EXCLUDED.label_3d,
            label_7d        = EXCLUDED.label_7d,
            label_14d       = EXCLUDED.label_14d,
            volatility_7d   = EXCLUDED.volatility_7d,
            volatility_30d  = EXCLUDED.volatility_30d,
            created_at      = EXCLUDED.created_at
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=1000)
    conn.commit()


def run(from_date: str | None = None, to_date: str | None = None):
    conn = get_db_conn()

    # Default: yesterday (incremental daily run)
    if not from_date:
        from_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    if not to_date:
        # Leave 14-day buffer so forward labels are computable
        to_date = (date.today() - timedelta(days=15)).strftime("%Y-%m-%d")
        if to_date < from_date:
            to_date = from_date

    log.info(f"Computing labels from {from_date} to {to_date}")

    rows = fetch_ohlcv_window(conn, from_date, to_date)
    log.info(f"Fetched {len(rows)} OHLCV rows from 1K_coins_ohlcv")

    label_rows = compute_labels(rows, from_date, to_date)
    log.info(f"Computed {len(label_rows)} label rows")

    if label_rows:
        upsert_labels(conn, label_rows)
        log.info(f"Upserted {len(label_rows)} rows into ML_LABELS")

    conn.close()
    return len(label_rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate ML_LABELS from OHLCV")
    parser.add_argument("--from-date", type=str, default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--to-date",   type=str, default=None, help="End date YYYY-MM-DD")
    args = parser.parse_args()
    run(from_date=args.from_date, to_date=args.to_date)
