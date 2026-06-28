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


# Backtest databases (cp_backtest / cp_backtest_h) may live on a different host
# than the primary `dbcp` warehouse. After the dbcp -> AWS RDS migration, dbcp is
# on RDS while the backtest DBs remain on GCP. Setting DB_BACKTEST_HOST (+ _USER /
# _PASSWORD / _SSLMODE) routes ONLY the backtest databases there; when unset, the
# backtest connections fall back to the primary DB_* vars (original behavior).
_BACKTEST_DBS = {"cp_backtest", "cp_backtest_h"}


def _connect(dbname):
    """Open a psycopg2 connection to `dbname`. The backtest DBs are routed to the
    DB_BACKTEST_* host/creds when DB_BACKTEST_HOST is set; everything else (incl. a
    bare `dbcp` fallback) stays on the primary DB_* host."""
    if dbname in _BACKTEST_DBS and os.environ.get("DB_BACKTEST_HOST", "").strip():
        host = os.environ["DB_BACKTEST_HOST"].strip()
        user = os.environ.get("DB_BACKTEST_USER", "").strip() or os.environ["DB_USER"]
        password = os.environ.get("DB_BACKTEST_PASSWORD", "").strip() or os.environ["DB_PASSWORD"]
        sslmode = os.environ.get("DB_BACKTEST_SSLMODE", "").strip()
    else:
        host = os.environ["DB_HOST"]
        user = os.environ["DB_USER"]
        password = os.environ["DB_PASSWORD"]
        sslmode = os.environ.get("DB_SSLMODE", "").strip()
    kwargs = dict(
        host=host,
        port=os.environ.get("DB_PORT", 5432),
        dbname=dbname,
        user=user,
        password=password,
    )
    if sslmode:
        kwargs["sslmode"] = sslmode
    return psycopg2.connect(**kwargs)


def get_backtest_conn():
    """Connect to the backtest DB (cp_backtest) for full historical feature data.
    Routed to DB_BACKTEST_* only when the resolved DB is a backtest DB; a bare
    fallback to DB_NAME (dbcp) stays on the primary host."""
    backtest_db = os.environ.get("DB_BACKTEST_NAME", "").strip()
    if not backtest_db:
        backtest_db = os.environ["DB_NAME"]
    return _connect(backtest_db)


def get_backtest_h_conn():
    """Connect to the hourly backtest DB (cp_backtest_h)."""
    return _connect("cp_backtest_h")
