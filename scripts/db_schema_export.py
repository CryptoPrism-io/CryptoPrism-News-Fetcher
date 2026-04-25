"""
Introspect all project databases and export a hierarchical schema reference.
Covers: dbcp, cp_backtest, cp_backtest_h
For each table: columns, types, PKs, indexes, row count, date range, distinct slugs.
"""
import psycopg2
import psycopg2.extras
import json
import os
import sys
from collections import OrderedDict
from dotenv import load_dotenv

sys.stdout.reconfigure(line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

DATABASES = {
    "dbcp": {
        "description": "Production DB — labels, signals, model registry, news, FE snapshots (latest date only for most FE tables)",
        "dbname": os.environ.get("DB_NAME", "dbcp"),
    },
    "cp_backtest": {
        "description": "Backtest DB — FULL historical FE data (years of history), residual features, cross-coin features",
        "dbname": "cp_backtest",
    },
    "cp_backtest_h": {
        "description": "Hourly backtest DB — 1h OHLCV candles for ~250 coins",
        "dbname": "cp_backtest_h",
    },
}


def get_conn(dbname):
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        dbname=dbname,
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def introspect_db(dbname, description):
    print(f"\n{'='*60}")
    print(f"  Introspecting: {dbname}")
    print(f"{'='*60}")
    conn = get_conn(dbname)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    db_info = OrderedDict()
    db_info["database"] = dbname
    db_info["description"] = description
    db_info["tables"] = OrderedDict()
    db_info["materialized_views"] = OrderedDict()

    # Get all tables (excluding system)
    cur.execute("""
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
    """)
    tables = [r["tablename"] for r in cur.fetchall()]

    # Get materialized views
    cur.execute("""
        SELECT matviewname FROM pg_matviews
        WHERE schemaname = 'public'
        ORDER BY matviewname
    """)
    matviews = [r["matviewname"] for r in cur.fetchall()]

    all_relations = [(t, "table") for t in tables] + [(m, "matview") for m in matviews]

    for rel_name, rel_type in all_relations:
        print(f"  {rel_type}: {rel_name}...", end=" ")
        info = OrderedDict()

        # Columns with types
        cur.execute("""
            SELECT a.attname AS column_name,
                   pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
                   a.attnotnull AS not_null
            FROM pg_attribute a
            JOIN pg_class c ON a.attrelid = c.oid
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE c.relname = %s AND n.nspname = 'public'
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
        """, (rel_name,))
        columns = []
        col_names = []
        for r in cur.fetchall():
            columns.append({
                "name": r["column_name"],
                "type": r["data_type"],
                "not_null": r["not_null"],
            })
            col_names.append(r["column_name"])
        info["columns"] = columns

        # Primary keys (tables only)
        if rel_type == "table":
            cur.execute("""
                SELECT a.attname
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                JOIN pg_class c ON c.oid = i.indrelid
                WHERE c.relname = %s AND i.indisprimary
                ORDER BY array_position(i.indkey, a.attnum)
            """, (rel_name,))
            pks = [r["attname"] for r in cur.fetchall()]
            info["primary_key"] = pks if pks else None

        # Indexes
        cur.execute("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = %s AND schemaname = 'public'
            ORDER BY indexname
        """, (rel_name,))
        indexes = []
        for r in cur.fetchall():
            indexes.append({
                "name": r["indexname"],
                "definition": r["indexdef"],
            })
        if indexes:
            info["indexes"] = indexes

        # Row count (approximate for speed)
        try:
            cur.execute(f"""
                SELECT reltuples::bigint AS estimate
                FROM pg_class WHERE relname = %s
            """, (rel_name,))
            r = cur.fetchone()
            approx_rows = r["estimate"] if r else -1
            if approx_rows < 1000:
                cur.execute(f'SELECT COUNT(*) FROM "{rel_name}"')
                exact = cur.fetchone()["count"]
                info["row_count"] = exact
                info["row_count_type"] = "exact"
            else:
                info["row_count"] = approx_rows
                info["row_count_type"] = "approximate"
        except Exception:
            conn.rollback()
            info["row_count"] = -1

        # Date range + slug count (if timestamp/slug columns exist)
        has_ts = "timestamp" in col_names
        has_slug = "slug" in col_names
        has_label_date = "label_date" in col_names

        ts_col = "timestamp" if has_ts else ("label_date" if has_label_date else None)

        if ts_col:
            try:
                cur.execute(f'SELECT MIN("{ts_col}")::date, MAX("{ts_col}")::date FROM "{rel_name}"')
                r = cur.fetchone()
                info["date_range"] = {
                    "min": str(r["min"]) if r["min"] else None,
                    "max": str(r["max"]) if r["max"] else None,
                    "column": ts_col,
                }
            except Exception:
                conn.rollback()

        if has_slug:
            try:
                cur.execute(f'SELECT COUNT(DISTINCT slug) FROM "{rel_name}"')
                r = cur.fetchone()
                info["distinct_slugs"] = r["count"]
            except Exception:
                conn.rollback()

        # Enum-like columns: sample distinct values for small-cardinality columns
        enum_candidates = ["status", "direction", "regime_state", "model_type",
                           "target", "is_active", "label_1d", "label_3d",
                           "sentiment", "fear_greed_sentiment"]
        for ec in enum_candidates:
            if ec in col_names:
                try:
                    cur.execute(f'SELECT DISTINCT "{ec}" FROM "{rel_name}" WHERE "{ec}" IS NOT NULL ORDER BY "{ec}" LIMIT 20')
                    vals = [str(r[ec]) for r in cur.fetchall()]
                    if vals and len(vals) <= 20:
                        info.setdefault("enum_values", {})[ec] = vals
                except Exception:
                    conn.rollback()

        target = db_info["tables"] if rel_type == "table" else db_info["materialized_views"]
        target[rel_name] = info
        rc = info.get("row_count", "?")
        dr = info.get("date_range", {})
        dr_str = f"{dr.get('min', '?')} to {dr.get('max', '?')}" if dr else "no dates"
        print(f"{rc:,} rows, {dr_str}")

    conn.close()
    return db_info


def build_cross_db_guide(schema):
    """Add a guide section showing which DB to query for what."""
    guide = OrderedDict()
    guide["_purpose"] = "Quick lookup: which DB has historical data for each FE table"

    fe_tables = [
        "FE_PCT_CHANGE", "FE_MOMENTUM_SIGNALS", "FE_OSCILLATORS_SIGNALS",
        "FE_TVV_SIGNALS", "FE_RATIOS_SIGNALS", "FE_RESIDUAL_FEATURES",
        "FE_CROSS_COIN", "FE_NEWS_SIGNALS", "FE_NEWS_EVENTS",
        "FE_FEAR_GREED_CMC", "FE_NEWS_SENTIMENT",
    ]

    for table in fe_tables:
        entry = {}
        for db_key, db_data in schema.items():
            if db_key.startswith("_"):
                continue
            tables = db_data.get("tables", {})
            mvs = db_data.get("materialized_views", {})
            if table in tables:
                t = tables[table]
                entry[db_key] = {
                    "rows": t.get("row_count", "?"),
                    "date_range": t.get("date_range", {}),
                    "slugs": t.get("distinct_slugs", "?"),
                }
            elif table in mvs:
                t = mvs[table]
                entry[db_key] = {
                    "rows": t.get("row_count", "?"),
                    "date_range": t.get("date_range", {}),
                }
        guide[table] = entry

    ml_tables = [
        "ML_LABELS", "ML_SIGNALS", "ML_SIGNALS_V2", "ML_MODEL_REGISTRY",
        "ML_TRADES", "ML_REGIME", "ML_LSTM_EMBEDDINGS", "ML_TCN_EMBEDDINGS",
    ]
    for table in ml_tables:
        entry = {}
        for db_key, db_data in schema.items():
            if db_key.startswith("_"):
                continue
            tables = db_data.get("tables", {})
            if table in tables:
                t = tables[table]
                entry[db_key] = {
                    "rows": t.get("row_count", "?"),
                    "date_range": t.get("date_range", {}),
                    "slugs": t.get("distinct_slugs", "?"),
                }
        guide[table] = entry

    return guide


def main():
    print("Database Schema Export")
    print("=" * 60)

    schema = OrderedDict()
    schema["_generated"] = "auto-generated by scripts/db_schema_export.py"
    schema["_host"] = os.environ["DB_HOST"]

    for db_key, db_meta in DATABASES.items():
        try:
            db_info = introspect_db(db_meta["dbname"], db_meta["description"])
            schema[db_key] = db_info
        except Exception as e:
            print(f"  ERROR introspecting {db_key}: {e}")
            schema[db_key] = {"error": str(e)}

    schema["_cross_db_guide"] = build_cross_db_guide(schema)

    out_path = os.path.join(ROOT, "src", "trading", "db_schema_full.json")
    with open(out_path, "w") as f:
        json.dump(schema, f, indent=2, default=str)
    print(f"\nSchema written to {out_path}")
    print(f"Total size: {os.path.getsize(out_path):,} bytes")

    # Print summary
    for db_key in DATABASES:
        if db_key in schema and "tables" in schema[db_key]:
            nt = len(schema[db_key]["tables"])
            nm = len(schema[db_key].get("materialized_views", {}))
            print(f"  {db_key}: {nt} tables, {nm} materialized views")


if __name__ == "__main__":
    main()
