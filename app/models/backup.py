from datetime import datetime
from sqlmodel import Field, SQLModel


class BackupSettings(SQLModel, table=True):
    __tablename__ = "tbl_backup_settings"

    id: int = Field(default=1, primary_key=True)
    google_email: str | None = None
    google_access_token: str | None = None
    google_refresh_token: str | None = None
    google_token_expiry: datetime | None = None
    drive_folder_id: str | None = None  # ID of "all_tables" folder in Drive
    last_backup_time: datetime | None = None
    last_backup_filename: str | None = None
