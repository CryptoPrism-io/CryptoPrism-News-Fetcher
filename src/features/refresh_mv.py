"""
refresh_mv.py
Refreshes mv_ml_feature_matrix after all upstream tables are updated.
CONCURRENTLY mode: no table lock, readers unaffected during refresh.

Usage:
    python -m src.features.refresh_mv
"""

import logging
import os

import psycopg2
from dotenv import load_dotenv
from src.db import get_db_conn


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)



def run():
    conn = get_db_conn()
    conn.autocommit = True  # required for REFRESH CONCURRENTLY

    # Check if already populated via catalog — first run must use plain REFRESH (no CONCURRENTLY)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ispopulated FROM pg_matviews
            WHERE matviewname = 'mv_ml_feature_matrix'
        """)
        row = cur.fetchone()
        is_populated = row[0] if row else False

    if not is_populated:
        log.info("Populating mv_ml_feature_matrix for the first time (no CONCURRENTLY)...")
        with conn.cursor() as cur:
            cur.execute('REFRESH MATERIALIZED VIEW "mv_ml_feature_matrix"')
    else:
        log.info("Refreshing mv_ml_feature_matrix CONCURRENTLY...")
        with conn.cursor() as cur:
            cur.execute('REFRESH MATERIALIZED VIEW CONCURRENTLY "mv_ml_feature_matrix"')

    # Report row count after refresh
    with conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM "mv_ml_feature_matrix"')
        count = cur.fetchone()[0]

    log.info(f"Refresh complete. mv_ml_feature_matrix rows: {count:,}")
    conn.close()
    return count


if __name__ == "__main__":
    run()
