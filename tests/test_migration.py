"""
Alembic migration 014: upgrade from scratch, downgrade to 013, upgrade again; then app start-up on top of it.  (T-36)
Uses its own throw-away database so the real migration chain (001 … 014) is exercised, not `create_all`.
"""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg2
import pytest

from tests.conftest import TEST_URL

BACKEND = Path(__file__).resolve().parents[1]
ALEMBIC_DB = "ggtest_alembic"


def _url_for(db: str) -> str:
    p = urlparse(TEST_URL)
    return urlunparse(p._replace(path=f"/{db}"))


def _connect(db: str):
    p = urlparse(TEST_URL)
    c = psycopg2.connect(host=p.hostname, port=p.port or 5432, user=p.username, password=p.password, dbname=db)
    c.autocommit = True
    return c


def _alembic(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": _url_for(ALEMBIC_DB), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env,
                          capture_output=True, text=True, timeout=300)


@pytest.fixture(scope="module")
def fresh_db():
    admin = _connect("postgres")
    cur = admin.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS {ALEMBIC_DB} WITH (FORCE)")
    cur.execute(f"CREATE DATABASE {ALEMBIC_DB}")
    admin.close()
    yield
    admin = _connect("postgres")
    admin.cursor().execute(f"DROP DATABASE IF EXISTS {ALEMBIC_DB} WITH (FORCE)")
    admin.close()


def _objects(cur):
    cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name IN "
                "('tbl_customer_alias','tbl_customer_ai_mismatch')")
    tables = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM information_schema.views WHERE table_name IN "
                "('vw_customer_alias','vw_customer_ai_mismatch')")
    views = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM pg_proc WHERE proname LIKE 'fn_ggfin_%'")
    funcs = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'trg_ggfin_%'")
    trigs = cur.fetchone()[0]
    return tables, views, funcs, trigs


def test_upgrade_downgrade_upgrade_cycle(fresh_db):
    r = _alembic("upgrade", "head")
    assert r.returncode == 0, r.stdout + r.stderr
    c = _connect(ALEMBIC_DB)
    cur = c.cursor()
    assert _objects(cur) == (2, 2, 5, 6)

    # the triggers installed by the migration actually work on the migrated schema
    cur.execute("INSERT INTO tbl_customer_alias (finance_type, customer_name, alias_name) VALUES ('EDI','Mig Test','mt')")
    cur.execute("INSERT INTO tbl_edi_customer (customer_id, customer_name) VALUES (500, 'Mig Test')")
    cur.execute("SELECT active_customer_id, is_customer_active FROM vw_customer_alias")
    assert cur.fetchone() == (500, True)
    cur.execute("UPDATE tbl_edi_customer SET customer_name = 'Mig Renamed' WHERE customer_id = 500")
    cur.execute("SELECT customer_name FROM tbl_customer_alias")
    assert cur.fetchone()[0] == "Mig Renamed"

    # downgrade removes only our objects, leaves customers alone
    r = _alembic("downgrade", "013_debt_loan_details")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _objects(cur) == (0, 0, 0, 0)
    cur.execute("SELECT count(*) FROM tbl_edi_customer")
    assert cur.fetchone()[0] == 1
    # customer tables are writable again (no dangling trigger functions)
    cur.execute("UPDATE tbl_edi_customer SET customer_name = 'After Downgrade' WHERE customer_id = 500")

    r = _alembic("upgrade", "head")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _objects(cur) == (2, 2, 5, 6)
    r = _alembic("current")
    assert "014_customer_name_mappings" in (r.stdout + r.stderr)
    c.close()


def test_single_alembic_head(fresh_db):
    r = _alembic("heads")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (r.stdout + r.stderr).count("(head)") == 1, r.stdout + r.stderr


def test_app_startup_on_top_of_the_migrated_schema_is_a_noop(fresh_db):
    """init_db (create_all + apply + resync) must coexist with the Alembic-created objects."""
    code = ("from app.core.database import init_db, engine\n"
            "init_db(); init_db()\n"
            "from sqlalchemy import text\n"
            "with engine.connect() as c:\n"
            "    print('TRIGGERS', c.execute(text(\"select count(*) from pg_trigger where tgname like 'trg_ggfin_%'\")).scalar())\n")
    env = {**os.environ, "DATABASE_URL": _url_for(ALEMBIC_DB), "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "TRIGGERS 6" in r.stdout
    assert "Could not install" not in r.stderr
