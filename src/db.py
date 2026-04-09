"""
db.py
Shared DB connection factory used by all ML pipeline modules.
Handles optional DB_SSLMODE — omits sslmode entirely if secret is not set or empty.
"""

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_db_conn():
    kwargs = dict(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", 5432),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )
    sslmode = os.environ.get("DB_SSLMODE", "").strip()
    if sslmode:
        kwargs["sslmode"] = sslmode
    return psycopg2.connect(**kwargs)


def get_backtest_conn():
    """Connect to the backtest DB (cp_backtest) for full historical feature data."""
    backtest_db = os.environ.get("DB_BACKTEST_NAME", "").strip()
    if not backtest_db:
        backtest_db = os.environ["DB_NAME"]
    kwargs = dict(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", 5432),
        dbname=backtest_db,
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )
    sslmode = os.environ.get("DB_SSLMODE", "").strip()
    if sslmode:
        kwargs["sslmode"] = sslmode
    return psycopg2.connect(**kwargs)


def get_backtest_h_conn():
    """Connect to the hourly backtest DB (cp_backtest_h)."""
    kwargs = dict(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", 5432),
        dbname="cp_backtest_h",
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )
    sslmode = os.environ.get("DB_SSLMODE", "").strip()
    if sslmode:
        kwargs["sslmode"] = sslmode
    return psycopg2.connect(**kwargs)
