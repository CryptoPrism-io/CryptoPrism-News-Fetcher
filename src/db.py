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
