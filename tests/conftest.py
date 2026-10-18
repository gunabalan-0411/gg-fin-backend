"""
Test harness. Triggers, views and `ON CONFLICT` are Postgres-specific, so tests run against a real Postgres:

    TEST_DATABASE_URL=postgresql://postgres@localhost:54329/ggtest python -m pytest tests

The database is WIPED (schema `public` is dropped) — the guard below refuses to run unless its name contains "test".
"""
import os
from urllib.parse import urlparse

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@localhost:54329/ggtest")
_dbname = urlparse(TEST_URL).path.lstrip("/")
if "test" not in _dbname:
    raise SystemExit(f"Refusing to run tests against database '{_dbname}': its name must contain 'test'")

# Must be set before any `app.*` import creates the engine.
os.environ["DATABASE_URL"] = TEST_URL
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

import app.models  # noqa: E402,F401  (register all tables)
from app.core.database import engine  # noqa: E402
from app.db import mapping_schema  # noqa: E402

DATA_TABLES = [
    "tbl_customer_alias", "tbl_customer_ai_mismatch",
    "tbl_edi_customer", "tbl_iop_customer",
    "tbl_edi_name_map", "tbl_iop_name_map",
    "tbl_edi_transactions", "tbl_iop_transactions",
    "tbl_upi_transactions", "tbl_upi_vpa_mappings", "tbl_drive_settings",
]


@pytest.fixture(scope="session", autouse=True)
def _schema():
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        mapping_schema.apply(conn)
    yield


@pytest.fixture(autouse=True)
def _reset_service_caches():
    from app.services import name_mapping_service
    name_mapping_service._trgm_available = None
    yield


@pytest.fixture(autouse=True)
def _clean():
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(DATA_TABLES) + " RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture()
def conn():
    """Autocommit-style connection for direct SQL (each statement commits)."""
    with engine.connect() as c:
        c = c.execution_options(isolation_level="AUTOCOMMIT")
        yield c


@pytest.fixture()
def session():
    with Session(engine) as s:
        yield s


@pytest.fixture()
def client():
    """FastAPI client with authentication bypassed (auth itself is not under test)."""
    from app.api.deps import get_current_user
    from app.main import app
    from app.models.user import User

    app.dependency_overrides[get_current_user] = lambda: User(id=1, username="tester", hashed_password="x")
    try:
        yield TestClient(app)  # no `with`: skip startup hooks (Drive auto-restore, Whisper download)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
