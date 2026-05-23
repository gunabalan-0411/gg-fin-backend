from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql://ggfin:ggfin_secret@localhost:5432/gg_fin_db"
    JWT_SECRET: str = "changeme-insecure-default"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours

    FIRST_SUPERUSER: str = "admin"
    FIRST_SUPERUSER_PASSWORD: str = "admin123"

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:3000/api/backup/oauth/callback"
    # Auto-derived from request host at runtime — override only if behind a non-standard proxy
    GMAIL_REDIRECT_URI: str = ""
    DRIVE_REDIRECT_URI: str = ""
    # Production frontend origin, e.g. https://gg-fin.railway.app (set in Railway env vars)
    FRONTEND_URL: str = "http://localhost:3000"

    # Set this on a fresh machine to auto-restore the DB from Drive on first boot.
    # Get the value from Settings → Google Drive → "Show Refresh Token" on the source machine.
    GOOGLE_DRIVE_REFRESH_TOKEN: str = ""


settings = Settings()

# Railway provides DATABASE_URL with "postgres://" prefix which SQLAlchemy rejects.
# Normalise it to "postgresql://" at module load time.
if settings.DATABASE_URL.startswith("postgres://"):
    settings.DATABASE_URL = settings.DATABASE_URL.replace("postgres://", "postgresql://", 1)
