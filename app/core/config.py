import logging
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql://ggfin:ggfin_secret@localhost:5432/gg_fin_db"
    JWT_SECRET: str = "changeme-insecure-default"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60  # 1 hour; refresh endpoint extends sessions

    FIRST_SUPERUSER: str = "admin"
    FIRST_SUPERUSER_PASSWORD: str = "admin123"

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:3000/api/backup/oauth/callback"
    GMAIL_REDIRECT_URI: str = ""
    DRIVE_REDIRECT_URI: str = ""
    FRONTEND_URL: str = "http://localhost:3000"
    GOOGLE_DRIVE_REFRESH_TOKEN: str = ""


settings = Settings()

# Warn loudly if insecure defaults are still active so operators notice in logs.
_INSECURE_JWT    = settings.JWT_SECRET == "changeme-insecure-default"
_INSECURE_PW     = settings.FIRST_SUPERUSER_PASSWORD == "admin123"
if _INSECURE_JWT:
    log.critical("⚠️  JWT_SECRET is the insecure default. Set a strong secret in environment variables before going to production.")
if _INSECURE_PW:
    log.warning("⚠️  FIRST_SUPERUSER_PASSWORD is 'admin123'. Change it via Settings → Change Password after first login.")

# Railway provides DATABASE_URL with "postgres://" prefix which SQLAlchemy rejects.
if settings.DATABASE_URL.startswith("postgres://"):
    settings.DATABASE_URL = settings.DATABASE_URL.replace("postgres://", "postgresql://", 1)
