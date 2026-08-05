"""
crypto_db — shared DB connection factory for the CryptoPrism fleet.

Used by both the ETL (etl/) and ML (ml/) packages so a single source of
truth owns connection logic (hosts, SSL mode, backtest DBs). To consume:

    pip install -e ./crypto_db
    from crypto_db import get_db_conn, get_backtest_conn, get_backtest_h_conn
"""
from crypto_db.db import (
    get_db_conn,
    get_backtest_conn,
    get_backtest_h_conn,
)

__all__ = ["get_db_conn", "get_backtest_conn", "get_backtest_h_conn"]
