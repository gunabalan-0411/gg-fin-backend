from __future__ import annotations

import io
import os
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlmodel import Session

from app.core.config import settings as app_settings
from app.models.backup import BackupSettings

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]


class BackupService:
    def __init__(self, session: Session):
        self.session = session

    # ── Settings singleton ────────────────────────────────────────────────

    def _get_or_create(self) -> BackupSettings:
        s = self.session.get(BackupSettings, 1)
        if not s:
            s = BackupSettings(id=1)
            self.session.add(s)
            self.session.commit()
            self.session.refresh(s)
        return s

    # ── Status ────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        s = self._get_or_create()
        connected = bool(s.google_refresh_token)
        backup_due = False
        if connected:
            if s.last_backup_time is None:
                backup_due = True
            else:
                last = s.last_backup_time
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                backup_due = (datetime.now(timezone.utc) - last).days >= 7
        return {
            "connected": connected,
            "google_email": s.google_email,
            "last_backup_time": s.last_backup_time.isoformat() if s.last_backup_time else None,
            "last_backup_filename": s.last_backup_filename,
            "backup_due": backup_due,
        }

    # ── OAuth ─────────────────────────────────────────────────────────────

    def _create_flow(self):
        from google_auth_oauthlib.flow import Flow

        client_config = {
            "web": {
                "client_id": app_settings.GOOGLE_CLIENT_ID,
                "client_secret": app_settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [app_settings.GOOGLE_REDIRECT_URI],
            }
        }
        return Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=app_settings.GOOGLE_REDIRECT_URI,
        )

    def get_auth_url(self) -> str:
        flow = self._create_flow()
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true",
        )
        return auth_url

    def handle_oauth_callback(self, code: str) -> None:
        from google.auth.transport.requests import AuthorizedSession

        flow = self._create_flow()
        flow.fetch_token(code=code)
        creds = flow.credentials

        authed = AuthorizedSession(creds)
        userinfo = authed.get("https://www.googleapis.com/oauth2/v2/userinfo").json()
        email = userinfo.get("email", "")

        s = self._get_or_create()
        s.google_email = email
        s.google_access_token = creds.token
        if creds.refresh_token:
            s.google_refresh_token = creds.refresh_token
        s.google_token_expiry = creds.expiry
        self.session.add(s)
        self.session.commit()

    def disconnect(self) -> None:
        s = self._get_or_create()
        s.google_email = None
        s.google_access_token = None
        s.google_refresh_token = None
        s.google_token_expiry = None
        s.drive_folder_id = None
        self.session.add(s)
        self.session.commit()

    # ── Drive helpers ─────────────────────────────────────────────────────

    def _get_drive_service(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        s = self._get_or_create()
        creds = Credentials(
            token=s.google_access_token,
            refresh_token=s.google_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=app_settings.GOOGLE_CLIENT_ID,
            client_secret=app_settings.GOOGLE_CLIENT_SECRET,
            scopes=SCOPES,
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            s.google_access_token = creds.token
            s.google_token_expiry = creds.expiry
            self.session.add(s)
            self.session.commit()
        return build("drive", "v3", credentials=creds)

    def _ensure_drive_folders(self, service) -> str:
        """Return the Drive ID of gg_fin/backup/all_tables, creating if needed."""

        def find_or_create(name: str, parent_id: str | None = None) -> str:
            q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            if parent_id:
                q += f" and '{parent_id}' in parents"
            results = service.files().list(q=q, fields="files(id)").execute()
            files = results.get("files", [])
            if files:
                return files[0]["id"]
            meta: dict = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
            if parent_id:
                meta["parents"] = [parent_id]
            return service.files().create(body=meta, fields="id").execute()["id"]

        gf = find_or_create("gg_fin")
        bf = find_or_create("backup", gf)
        return find_or_create("all_tables", bf)

    # ── pg_dump / psql ────────────────────────────────────────────────────

    def _db_parts(self) -> dict:
        parsed = urlparse(app_settings.DATABASE_URL)
        return {
            "host": parsed.hostname or "postgres",
            "port": str(parsed.port or 5432),
            "user": parsed.username or "ggfin",
            "password": parsed.password or "",
            "dbname": (parsed.path or "/gg_fin_db").lstrip("/"),
        }

    def _run_pg_dump(self) -> tuple[str, str]:
        db = self._db_parts()
        filename = f"{datetime.now().strftime('%Y-%m-%d')}_full_backup.sql"
        tmp_path = f"/tmp/{filename}"
        env = {**os.environ, "PGPASSWORD": db["password"]}
        result = subprocess.run(
            ["pg_dump", "-h", db["host"], "-p", db["port"], "-U", db["user"],
             "-d", db["dbname"], "--clean", "--if-exists", "-f", tmp_path],
            env=env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"pg_dump failed: {result.stderr}")
        return tmp_path, filename

    # ── Push ──────────────────────────────────────────────────────────────

    def push_backup(self) -> dict:
        from googleapiclient.http import MediaFileUpload

        s = self._get_or_create()
        if not s.google_refresh_token:
            raise ValueError("Google account not connected")

        tmp_path, filename = self._run_pg_dump()
        try:
            service = self._get_drive_service()
            folder_id = self._ensure_drive_folders(service)

            # Delete previous backup to avoid duplicates
            if s.last_backup_filename:
                q = (
                    f"name='{s.last_backup_filename}'"
                    f" and '{folder_id}' in parents and trashed=false"
                )
                for f in service.files().list(q=q, fields="files(id)").execute().get("files", []):
                    service.files().delete(fileId=f["id"]).execute()

            media = MediaFileUpload(tmp_path, mimetype="application/sql", resumable=True)
            uploaded = service.files().create(
                body={"name": filename, "parents": [folder_id]},
                media_body=media, fields="id",
            ).execute()

            s.drive_folder_id = folder_id
            s.last_backup_time = datetime.now(timezone.utc)
            s.last_backup_filename = filename
            self.session.add(s)
            self.session.commit()

            return {"success": True, "filename": filename, "file_id": uploaded.get("id")}
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # ── Pull ──────────────────────────────────────────────────────────────

    def pull_backup(self) -> dict:
        from googleapiclient.http import MediaIoBaseDownload

        s = self._get_or_create()
        if not s.google_refresh_token:
            raise ValueError("Google account not connected")
        if not s.drive_folder_id or not s.last_backup_filename:
            raise ValueError("No backup found on Drive")

        service = self._get_drive_service()
        q = (
            f"name='{s.last_backup_filename}'"
            f" and '{s.drive_folder_id}' in parents and trashed=false"
        )
        files = service.files().list(q=q, fields="files(id)").execute().get("files", [])
        if not files:
            raise ValueError("Backup file not found on Drive")

        tmp_path = f"/tmp/restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"
        request = service.files().get_media(fileId=files[0]["id"])
        with open(tmp_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        try:
            db = self._db_parts()
            env = {**os.environ, "PGPASSWORD": db["password"]}
            result = subprocess.run(
                ["psql", "-h", db["host"], "-p", db["port"], "-U", db["user"],
                 "-d", db["dbname"], "-f", tmp_path],
                env=env, capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"psql restore failed: {result.stderr}")
            return {"success": True, "filename": s.last_backup_filename}
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
