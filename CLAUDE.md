# CryptoPrism News Fetcher — Project Rules

## DB Schema Gate (MANDATORY)

Before writing ANY SQL query, `pd.read_sql`, `psycopg2` query, DB migration, or code that reads/writes PostgreSQL tables:

1. **Invoke the `db-schema-lookup` skill** OR read `src/trading/db_schema_full.json` directly
2. Confirm which database (dbcp / cp_backtest / cp_backtest_h) has the table you need
3. Verify column names, date ranges, and row counts match your assumptions

**Why:** This project has three databases where the same table name (e.g. `FE_MOMENTUM_SIGNALS`) exists in multiple DBs with vastly different data — dbcp has a 1-date snapshot (~1K rows), cp_backtest has years of history (~1M rows). Querying the wrong one produces silent failures (null features, empty results, garbage backtests).

**Schema JSON:** `src/trading/db_schema_full.json` (regenerate via `scripts/db_schema_export.py` on GitHub Actions)

**Postgres MCP servers** (live queries — use to verify schema or run exploratory queries):
- `mcp__postgres__query` → **dbcp** (production)
- `mcp__postgres-backtest__query` → **cp_backtest** (full history)
- `mcp__postgres-hourly__query` → **cp_backtest_h** (hourly)

**Quick reference — which DB for what:**
- Historical FE features (backtesting) → **cp_backtest**
- Today's FE snapshot (inference) → **dbcp**
- ML signals, labels, trades, regime → **dbcp**
- LSTM/TCN embeddings → **cp_backtest**
- News, sentiment, Fear & Greed → **dbcp**
- Hourly OHLCV/features → **cp_backtest_h**
- Daily OHLCV → **dbcp** or **cp_backtest** (both have `1K_coins_ohlcv`)

## Network Constraints

- Local machine CANNOT run `pip install` from Claude Code (antivirus blocks it). User must install packages manually.
- DB-heavy scripts (bulk reads/writes) → run on **GitHub Actions** (`universe-backtest.yml` workflow), not locally. GCP-to-GCP is fast; local-to-GCP hangs.

## Zero-Touch Contract

NEVER ALTER, DROP, or INSERT into existing production tables (FE_*, OHLCV, cc_news) without explicit user permission. New tables and migrations go in `migrations/`.
