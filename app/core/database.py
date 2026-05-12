from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings

engine = create_engine(settings.DATABASE_URL, echo=False)


def init_db() -> None:
    # Advisory lock prevents multiple gunicorn workers from racing on CREATE TABLE
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(987654321)"))
        SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
