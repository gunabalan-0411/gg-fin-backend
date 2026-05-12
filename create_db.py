"""Ensures the target database exists before alembic runs."""
import os
import sys
from urllib.parse import urlparse

import psycopg2

url = urlparse(os.environ["DATABASE_URL"])
db_name = url.path.lstrip("/")

try:
    conn = psycopg2.connect(
        host=url.hostname,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        dbname="postgres",
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
    if not cur.fetchone():
        cur.execute(f'CREATE DATABASE "{db_name}"')
        print(f"[startup] Created database: {db_name}")
    else:
        print(f"[startup] Database already exists: {db_name}")
    cur.close()
    conn.close()
except Exception as e:
    print(f"[startup] ERROR: {e}", file=sys.stderr)
    sys.exit(1)
