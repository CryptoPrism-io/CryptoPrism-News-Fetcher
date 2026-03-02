"""
refresh_mv.py
Refreshes materialized views after all upstream tables are updated.
CONCURRENTLY mode: no table lock, readers unaffected during refresh.

Views refreshed:
  mv_ml_feature_matrix   — training matrix (anchored on ML_LABELS)
  mv_ml_inference_matrix — inference matrix (anchored on FE_PCT_CHANGE,
                           always has today/yesterday even without labels)

Usage:
    python -m src.features.refresh_mv              # both MVs
    python -m src.features.refresh_mv --inference  # inference MV only (fast)
"""

import argparse
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


def _refresh_mv(conn, mv_name: str) -> int:
    """Refresh a single materialized view. Returns final row count."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ispopulated FROM pg_matviews WHERE matviewname = %s",
            (mv_name,),
        )
        row = cur.fetchone()

    if row is None:
        log.warning(f"{mv_name} does not exist — skipping (run the migration first).")
        return 0

    if not row[0]:
        log.info(f"Populating {mv_name} for the first time (no CONCURRENTLY)...")
        with conn.cursor() as cur:
            cur.execute(f'REFRESH MATERIALIZED VIEW "{mv_name}"')
    else:
        log.info(f"Refreshing {mv_name} CONCURRENTLY...")
        with conn.cursor() as cur:
            cur.execute(f'REFRESH MATERIALIZED VIEW CONCURRENTLY "{mv_name}"')

    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{mv_name}"')
        count = cur.fetchone()[0]

    log.info(f"Refresh complete. {mv_name} rows: {count:,}")
    return count


def run(inference_only: bool = False):
    conn = get_db_conn()
    conn.autocommit = True  # required for REFRESH CONCURRENTLY

    results = {}

    if not inference_only:
        results["mv_ml_feature_matrix"] = _refresh_mv(conn, "mv_ml_feature_matrix")

    results["mv_ml_inference_matrix"] = _refresh_mv(conn, "mv_ml_inference_matrix")

    conn.close()
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--inference", action="store_true",
        help="Refresh inference MV only (skip training MV — faster for daily signals run)",
    )
    args = parser.parse_args()
    run(inference_only=args.inference)
