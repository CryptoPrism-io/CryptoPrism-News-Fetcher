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

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def get_db_conn():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", 5432),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "require"),
    )


def run():
    conn = get_db_conn()
    conn.autocommit = True  # required for REFRESH CONCURRENTLY

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
