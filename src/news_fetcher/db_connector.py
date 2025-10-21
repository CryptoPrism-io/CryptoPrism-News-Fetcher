import os
import psycopg2
from psycopg2.extras import execute_values

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")


def push_headlines(records: list, table: str = "news_headlines") -> None:
    """
    Push organised headlines to SQL database.

    :param records: List of dicts with keys 'headline' and 'fetched_at'
    :param table: Table name to insert into
    """
    conn = psycopg2.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        dbname=DB_NAME,
    )
    cur = conn.cursor()
    rows = [(r['headline'], r['fetched_at']) for r in records]
    query = f"INSERT INTO {table} (headline, fetched_at) VALUES %s"
    execute_values(cur, query, rows)
    conn.commit()
    cur.close()
    conn.close()
