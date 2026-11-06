"""The ML_REGIME writer: what `regime --update` leaves in the table.

Drives the real entry point in a subprocess against a disposable local Postgres.
DB_HOST is always set explicitly, and load_dotenv never overrides it, so these
tests can only reach the cluster they start.
"""
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest

REPO = Path(__file__).parent.parent
MIGRATION = REPO / "migrations" / "012_ml_regime.sql"
WINDOWS_PG_BIN = Path(r"C:\Program Files\PostgreSQL\16\bin")
TODAY = datetime.now(timezone.utc).date()
SENTINEL = "provisional-sentinel"


def _pg_binary(name: str) -> str | None:
    windows = WINDOWS_PG_BIN / f"{name}.exe"
    return str(windows) if windows.exists() else shutil.which(name)


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    """A throwaway Postgres in UTC, like RDS. Yields its port."""
    initdb, pg_ctl = _pg_binary("initdb"), _pg_binary("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.skip(f"Postgres binaries not found in {WINDOWS_PG_BIN} or on PATH")

    data = tmp_path_factory.mktemp("pg") / "data"
    init = subprocess.run(
        [initdb, "-D", str(data), "-U", "postgres", "-A", "trust", "--no-sync", "-E", "UTF8"],
        capture_output=True, text=True,
    )
    if init.returncode != 0:
        pytest.skip(f"initdb could not create a cluster: {init.stderr.strip()}")

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    options = f"-p {port} -c listen_addresses=127.0.0.1 -c timezone=UTC"
    # Output goes to a file: postgres inherits these handles and would hold a pipe open.
    subprocess.run(
        [pg_ctl, "-D", str(data), "-o", options, "-l", str(data / "server.log"), "-w", "start"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    yield port
    subprocess.run(
        [pg_ctl, "-D", str(data), "-m", "immediate", "-w", "stop"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@pytest.fixture
def db(cluster, request):
    """A fresh database with the three tables the writer touches."""
    name = "t_" + request.node.name.lower()[:50]
    admin = psycopg2.connect(host="127.0.0.1", port=cluster, dbname="postgres", user="postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()

    conn = psycopg2.connect(host="127.0.0.1", port=cluster, dbname=name, user="postgres")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(MIGRATION.read_text())
        cur.execute("""
            CREATE TABLE "1K_coins_ohlcv" (
                slug TEXT, timestamp TIMESTAMPTZ, close DOUBLE PRECISION,
                volume DOUBLE PRECISION, market_cap DOUBLE PRECISION,
                PRIMARY KEY (slug, timestamp)
            )
        """)
        cur.execute('CREATE TABLE "FE_FEAR_GREED_CMC" (timestamp TIMESTAMP, fear_greed_index BIGINT)')
    yield conn
    conn.close()


def _day(days_ago: int) -> datetime:
    d = TODAY - timedelta(days=days_ago)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _add_candle(conn, days_ago: int):
    """A candle for that day, stamped 23:59:59 as DB-D stamps them."""
    ts = _day(days_ago) + timedelta(hours=23, minutes=59, seconds=59)
    close = 100 + (days_ago % 11) * 2 + (200 - days_ago) * 0.05
    with conn.cursor() as cur:
        cur.execute('INSERT INTO "1K_coins_ohlcv" VALUES (%s, %s, %s, 1, 1e9)', ("bitcoin", ts, close))


def _seed_market(conn, newest: int = 1, skip: tuple = (), fgi_newest: int | None = None):
    """120 days of candles and Fear & Greed, ending `newest` days ago."""
    for days_ago in range(120, newest - 1, -1):
        if days_ago not in skip:
            _add_candle(conn, days_ago)
    with conn.cursor() as cur:
        for days_ago in range(120, (newest if fgi_newest is None else fgi_newest) - 1, -1):
            cur.execute('INSERT INTO "FE_FEAR_GREED_CMC" VALUES (%s, 45)', (_day(days_ago).replace(tzinfo=None),))


def _seed_regime(conn, days_ago: int, state: str = "risk_on"):
    with conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "ML_REGIME" (timestamp, regime_state, confidence) VALUES (%s, %s, 0.5)',
            (_day(days_ago), state),
        )


def _run_update(conn) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "DB_HOST": "127.0.0.1", "DB_PORT": str(conn.info.port), "DB_NAME": conn.info.dbname,
        "DB_USER": "postgres", "DB_PASSWORD": "unused", "DB_SSLMODE": "",
    }
    return subprocess.run(
        [sys.executable, "-W", "ignore", "-m", "ml.models.regime", "--update"],
        cwd=REPO, env=env, capture_output=True, text=True,
    )


def _regime_table(conn) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT timestamp, regime_state, confidence, trans_prob_risk_on
            FROM "ML_REGIME" ORDER BY timestamp
        """)
        return [dict(r) for r in cur.fetchall()]


def _state_on(conn, days_ago: int) -> str | None:
    rows = [r for r in _regime_table(conn) if r["timestamp"] == _day(days_ago)]
    return rows[0]["regime_state"] if rows else None


def test_stale_feed_is_refused_and_nothing_is_written(db):
    _seed_market(db, newest=5)
    _seed_regime(db, days_ago=30)
    before = _regime_table(db)

    result = _run_update(db)

    assert result.returncode != 0
    assert "StaleMarketData" in result.stderr
    assert _regime_table(db) == before


def test_day_without_a_candle_is_named_in_a_warning(db):
    _seed_market(db, skip=(12,))
    _seed_regime(db, days_ago=1)

    result = _run_update(db)

    missing = (TODAY - timedelta(days=12)).isoformat()
    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stdout and missing in result.stdout


def test_stale_fear_and_greed_is_warned_about(db):
    _seed_market(db, fgi_newest=10)

    result = _run_update(db)

    newest_fgi = (TODAY - timedelta(days=10)).isoformat()
    assert result.returncode == 0, result.stderr
    assert "Fear & Greed" in result.stdout and newest_fgi in result.stdout


def test_late_candle_replaces_a_provisional_row(db):
    """OHLCV can miss a day and backfill it weeks later."""
    _seed_market(db, skip=(20,))
    _seed_regime(db, days_ago=20, state=SENTINEL)
    _seed_regime(db, days_ago=1)

    assert _run_update(db).returncode == 0
    assert _state_on(db, 20) == SENTINEL

    _add_candle(db, 20)
    assert _run_update(db).returncode == 0
    assert _state_on(db, 20) != SENTINEL


def test_rerun_changes_nothing(db):
    _seed_market(db)
    _seed_regime(db, days_ago=30)

    assert _run_update(db).returncode == 0
    first = _regime_table(db)
    assert _run_update(db).returncode == 0

    assert _regime_table(db) == first


def test_first_run_after_a_freeze_fills_the_gap_up_to_today(db):
    _seed_market(db)
    frozen = 60
    _seed_regime(db, days_ago=frozen)

    result = _run_update(db)

    assert result.returncode == 0, result.stderr
    written = {r["timestamp"] for r in _regime_table(db)}
    assert {_day(n) for n in range(frozen, -1, -1)} <= written
    assert _regime_table(db)[-1]["timestamp"] == _day(0)
