"""Print the current Postgres server identity so we know where it lives
post-AWS-migration (RDS vs GCE) and what IP to allowlist."""
import os
import psycopg2

conn = psycopg2.connect(
    host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
    user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
    dbname=os.environ["DB_NAME"],
)
cur = conn.cursor()
cur.execute("SELECT current_database(), inet_server_addr(), inet_server_port(), version();")
db, addr, port, ver = cur.fetchone()
print(f"database        : {db}")
print(f"server address  : {addr}")   # 10.x/172.x = private (likely RDS/VPC); public = exposed
print(f"server port     : {port}")
print(f"version         : {ver}")
# RDS builds usually mention 'Amazon' or a distinct build; GCE self-managed does not.
print(f"looks_like_rds  : {'amazon' in ver.lower() or 'rds' in ver.lower()}")
cur.execute("SELECT count(*) FROM cc_news;")
print(f"cc_news rows    : {cur.fetchone()[0]}")
cur.close()
conn.close()
