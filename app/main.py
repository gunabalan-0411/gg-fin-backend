import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, backup, customers, dashboard, dataset, debts, defaulted_balances, drive, expenses, namemap, setup, sql, transactions, unclaimed_balances, voice, upi
from app.core.database import init_db
from app.core.config import settings

app = FastAPI(title="GG Finance API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()
    # Auto-restore from Drive on fresh machines (GOOGLE_DRIVE_REFRESH_TOKEN in .env)
    try:
        from app.services.drive_service import auto_restore_on_startup
        auto_restore_on_startup()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("auto_restore_on_startup skipped: %s", exc)
    _seed_admin()


def _seed_admin():
    from sqlmodel import Session, select
    from sqlalchemy.exc import IntegrityError
    from app.core.database import engine
    from app.models.user import User
    from app.core.security import hash_password

    with Session(engine) as session:
        existing = session.exec(
            select(User).where(User.username == settings.FIRST_SUPERUSER)
        ).first()
        if not existing:
            user = User(
                username=settings.FIRST_SUPERUSER,
                hashed_password=hash_password(settings.FIRST_SUPERUSER_PASSWORD),
            )
            session.add(user)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()  # another worker already inserted it, fine


app.include_router(setup.router, prefix="/api/setup", tags=["setup"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(customers.router, prefix="/api/customers", tags=["customers"])
app.include_router(transactions.router, prefix="/api/transactions", tags=["transactions"])
app.include_router(expenses.router, prefix="/api/expenses", tags=["expenses"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(voice.router, prefix="/api/voice", tags=["voice"])
try:
    from app.api import ocr
    app.include_router(ocr.router, prefix="/api/ocr", tags=["ocr"])
except Exception as _ocr_err:
    logging.getLogger(__name__).warning("OCR router disabled — missing dependency: %s", _ocr_err)
app.include_router(backup.router, prefix="/api/backup", tags=["backup"])
app.include_router(namemap.router, prefix="/api/namemap", tags=["namemap"])
app.include_router(dataset.router, prefix="/api/dataset", tags=["dataset"])
app.include_router(upi.router, prefix="/api/upi", tags=["upi"])
app.include_router(drive.router, prefix="/api/drive", tags=["drive"])
app.include_router(debts.router, prefix="/api/debts", tags=["debts"])
app.include_router(unclaimed_balances.router, prefix="/api/unclaimed-balances", tags=["unclaimed-balances"])
app.include_router(defaulted_balances.router, prefix="/api/defaulted-balances", tags=["defaulted-balances"])
app.include_router(sql.router, prefix="/api/sql", tags=["sql"])


@app.get("/health")
def health():
    return {"status": "ok"}
