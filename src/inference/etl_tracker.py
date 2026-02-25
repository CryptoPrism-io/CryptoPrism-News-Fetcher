"""
etl_tracker.py
Plugs into existing etl_runs + etl_job_stats tables (read/write allowed — these are infra tables).
Wraps any job with start/end timing and status logging.
Never touches FE_* or OHLCV tables.
"""

import os
import time
import logging
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
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


def _start_run(conn, job_name: str) -> int:
    sql = """
        INSERT INTO etl_runs (job_name, start_time, status, created_at)
        VALUES (%s, %s, 'running', %s)
        RETURNING run_id
    """
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(sql, (job_name, now, now))
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def _end_run(conn, run_id: int, status: str, rows: int, error: str | None, start_time: float):
    end_time = datetime.now(timezone.utc)
    duration_minutes = int((time.time() - start_time) / 60)
    sql = """
        UPDATE etl_runs
        SET end_time = %s, status = %s, rows_processed = %s,
            error_message = %s, duration_minutes = %s
        WHERE run_id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (end_time, status, rows, error, duration_minutes, run_id))

    # Upsert into etl_job_stats
    stats_sql = """
        INSERT INTO etl_job_stats (
            job_name, total_runs, successful_runs, failed_runs,
            avg_duration_minutes, last_run_time, last_run_status,
            total_rows_processed, updated_at
        ) VALUES (%s, 1, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (job_name) DO UPDATE SET
            total_runs             = etl_job_stats.total_runs + 1,
            successful_runs        = etl_job_stats.successful_runs + EXCLUDED.successful_runs,
            failed_runs            = etl_job_stats.failed_runs + EXCLUDED.failed_runs,
            avg_duration_minutes   = (etl_job_stats.avg_duration_minutes * etl_job_stats.total_runs
                                      + EXCLUDED.avg_duration_minutes) / (etl_job_stats.total_runs + 1),
            last_run_time          = EXCLUDED.last_run_time,
            last_run_status        = EXCLUDED.last_run_status,
            total_rows_processed   = etl_job_stats.total_rows_processed + EXCLUDED.total_rows_processed,
            updated_at             = EXCLUDED.updated_at
    """
    success_inc = 1 if status == "success" else 0
    fail_inc = 1 if status == "failed" else 0
    with conn.cursor() as cur:
        cur.execute(stats_sql, (
            job_name, success_inc, fail_inc, duration_minutes,
            end_time, status, rows, end_time,
        ))
    conn.commit()


@contextmanager
def track(job_name: str):
    """
    Context manager. Wraps a job with etl_runs tracking.

    Usage:
        with track("nlp_sentiment_scoring") as t:
            rows = run_scoring()
            t.rows = rows   # optional: set row count
    """
    conn = get_db_conn()
    run_id = _start_run(conn, job_name)
    start_time = time.time()
    log.info(f"[etl_tracker] Started job '{job_name}' (run_id={run_id})")

    tracker = type("Tracker", (), {"rows": 0})()
    try:
        yield tracker
        _end_run(conn, run_id, "success", tracker.rows, None, start_time)
        log.info(f"[etl_tracker] Job '{job_name}' completed. rows={tracker.rows}")
    except Exception as e:
        err = traceback.format_exc()
        _end_run(conn, run_id, "failed", tracker.rows, err[:2000], start_time)
        log.error(f"[etl_tracker] Job '{job_name}' FAILED: {e}")
        raise
    finally:
        conn.close()
