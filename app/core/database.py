import logging

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings

log = logging.getLogger(__name__)

engine = create_engine(settings.DATABASE_URL, echo=False)


def init_db() -> None:
    # Advisory lock prevents multiple gunicorn workers from racing on CREATE TABLE
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(987654321)"))
        SQLModel.metadata.create_all(engine)

    # Alias / AI-mismatch mapping triggers + views (idempotent). Re-applied on every start because restoring an
    # older backup re-creates the customer tables without our triggers.
    try:
        from app.db import mapping_schema

        with engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(987654322)"))
            mapping_schema.apply(conn)
            mapping_schema.resync_all(conn)
    except Exception:
        log.exception("Could not install the name-mapping triggers/views — mapping pointers will NOT auto-update")


def get_session():
    with Session(engine) as session:
        yield session
