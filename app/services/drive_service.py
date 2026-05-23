"""Google Drive integration — OAuth, export (pg_dump → Drive), import (Drive → restore)."""
from __future__ import annotations

import io
import logging
import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

from sqlmodel import Session

from app.core.config import settings
from app.models.upi import DriveSettings

_DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "openid",
    "email",
]
_GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
_GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

# Nested folder path: My Drive → gg_fin → db_bck_up
_FOLDER_PATH = ["gg_fin", "db_bck_up"]
_MAX_BACKUPS = 10


# ── OAuth ─────────────────────────────────────────────────────────────────────

def get_auth_url(redirect_uri: str) -> str:
    import os as _os
    _os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    from requests_oauthlib import OAuth2Session  # type: ignore

    oauth = OAuth2Session(
        client_id=settings.GOOGLE_CLIENT_ID,
        redirect_uri=redirect_uri,
        scope=_DRIVE_SCOPES,
    )
    auth_url, _ = oauth.authorization_url(
        _GOOGLE_AUTH_URI,
        access_type="offline",
        prompt="consent",
    )
    return auth_url


def exchange_code(code: str, redirect_uri: str, session: Session) -> str:
    import requests as http_requests

    resp = http_requests.post(
        _GOOGLE_TOKEN_URI,
        data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    token_data = resp.json()

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token") or None
    expires_in = token_data.get("expires_in", 3600)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    ui_resp = http_requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    email = ui_resp.json().get("email", "") if ui_resp.ok else ""

    d = session.get(DriveSettings, 1) or DriveSettings(id=1)
    d.email = email
    d.access_token = access_token
    d.refresh_token = refresh_token
    d.token_expiry = expiry
    session.add(d)
    session.commit()
    return email


def get_status(session: Session) -> dict:
    d = session.get(DriveSettings, 1)
    if not d or not d.access_token:
        return {"connected": False, "email": None}
    return {"connected": True, "email": d.email}


def disconnect(session: Session) -> None:
    d = session.get(DriveSettings, 1)
    if d:
        d.access_token = None
        d.refresh_token = None
        d.token_expiry = None
        session.add(d)
        session.commit()


def _build_drive_service(d: DriveSettings, session: Session):
    try:
        from google.oauth2.credentials import Credentials  # type: ignore
        from google.auth.transport.requests import Request  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
    except ImportError:
        raise RuntimeError("google-api-python-client is not installed.")

    expiry = d.token_expiry
    if expiry is not None and expiry.tzinfo is not None:
        expiry = expiry.replace(tzinfo=None)  # google-auth expects naive UTC

    creds = Credentials(
        token=d.access_token,
        refresh_token=d.refresh_token,
        token_uri=_GOOGLE_TOKEN_URI,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=_DRIVE_SCOPES,
        expiry=expiry,
    )
    if creds.refresh_token and (not creds.expiry or creds.expired):
        try:
            creds.refresh(Request())
        except Exception as e:
            raise RuntimeError(
                f"Drive token refresh failed — please reconnect Google Drive. ({e})"
            ) from e
        d.access_token = creds.token
        d.token_expiry = creds.expiry
        session.add(d)
        session.commit()

    return build("drive", "v3", credentials=creds)


def _get_folder_by_path(service, path_parts: list) -> str:
    """Navigate or create nested folders and return the final folder ID."""
    parent_id = "root"
    for part in path_parts:
        query = (
            f"name='{part}' and "
            f"'{parent_id}' in parents and "
            f"mimeType='application/vnd.google-apps.folder' and trashed=false"
        )
        results = service.files().list(q=query, fields="files(id)", pageSize=5).execute()
        files = results.get("files", [])
        if files:
            parent_id = files[0]["id"]
        else:
            folder = service.files().create(
                body={
                    "name": part,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [parent_id],
                },
                fields="id",
            ).execute()
            parent_id = folder["id"]
    return parent_id


def _prune_old_backups(service, folder_id: str, keep: int = _MAX_BACKUPS) -> int:
    """Delete backups older than the `keep` most recent. Returns count deleted."""
    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        orderBy="modifiedTime desc",
        fields="files(id, name, modifiedTime)",
        pageSize=100,
    ).execute()
    files = results.get("files", [])
    to_delete = files[keep:]
    for f in to_delete:
        try:
            service.files().delete(fileId=f["id"]).execute()
            logger.info("Drive: pruned old backup %s", f["name"])
        except Exception as e:
            logger.warning("Drive: failed to delete %s: %s", f["name"], e)
    return len(to_delete)


# ── Export ────────────────────────────────────────────────────────────────────

def export_to_drive(session: Session) -> dict:
    """Run pg_dump and upload to Drive at gg_fin/db_bck_up, keeping last 10."""
    d = session.get(DriveSettings, 1)
    if not d or not d.access_token:
        raise ValueError("Google Drive not connected.")

    parsed = urlparse(settings.DATABASE_URL)
    db = {
        "host": parsed.hostname or "postgres",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "ggfin",
        "password": parsed.password or "",
        "dbname": (parsed.path or "/gg_fin_db").lstrip("/"),
    }

    filename = f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}_gg_fin_backup.sql"
    tmp_path = f"/tmp/{filename}"

    try:
        # 1. Run pg_dump (120-second hard timeout)
        env = {**os.environ, "PGPASSWORD": db["password"]}
        result = subprocess.run(
            ["pg_dump", "-h", db["host"], "-p", db["port"], "-U", db["user"],
             "-d", db["dbname"], "--clean", "--if-exists", "-f", tmp_path],
            env=env, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"pg_dump failed: {result.stderr[:500]}")

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise RuntimeError("pg_dump produced an empty file.")

        # 2. Build Drive service (token refresh happens here)
        service = _build_drive_service(d, session)

        # 3. Upload
        from googleapiclient.http import MediaFileUpload  # type: ignore
        folder_id = _get_folder_by_path(service, _FOLDER_PATH)

        media = MediaFileUpload(tmp_path, mimetype="application/octet-stream", resumable=True)
        file_meta = service.files().create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id, name, webViewLink",
        ).execute()

        deleted = _prune_old_backups(service, folder_id)
        logger.info("Drive export: uploaded %s, pruned %d old backups", filename, deleted)

        return {
            "file_id": file_meta["id"],
            "file_name": file_meta["name"],
            "web_view_link": file_meta.get("webViewLink"),
            "pruned": deleted,
        }
    except subprocess.TimeoutExpired:
        raise RuntimeError("pg_dump timed out after 120 seconds.")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ── List files ────────────────────────────────────────────────────────────────

def list_files(session: Session) -> list:
    """List backup files in gg_fin/db_bck_up, newest first."""
    d = session.get(DriveSettings, 1)
    if not d or not d.access_token:
        raise ValueError("Google Drive not connected.")

    service = _build_drive_service(d, session)
    folder_id = _get_folder_by_path(service, _FOLDER_PATH)

    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        orderBy="modifiedTime desc",
        fields="files(id, name, size, modifiedTime)",
        pageSize=_MAX_BACKUPS + 5,
    ).execute()
    return results.get("files", [])


# ── Import from Drive ─────────────────────────────────────────────────────────

def start_import_job(file_id: str, session: Session) -> str:
    """Download a backup from Drive and start a background restore job."""
    from app.api.backup import _jobs, _do_restore

    d = session.get(DriveSettings, 1)
    if not d or not d.access_token:
        raise ValueError("Google Drive not connected.")

    service = _build_drive_service(d, session)

    file_meta = service.files().get(fileId=file_id, fields="name").execute()
    filename = file_meta.get("name", "backup.sql")

    from googleapiclient.http import MediaIoBaseDownload  # type: ignore
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    content = buf.getvalue()

    if not content:
        raise RuntimeError("Downloaded file is empty.")

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"progress": 5, "status": "running", "message": "Downloaded from Drive, starting restore…"}
    t = threading.Thread(target=_do_restore, args=(content, filename, job_id), daemon=True)
    t.start()
    return job_id


# ── Startup auto-restore ──────────────────────────────────────────────────────

def _build_drive_service_from_token(refresh_token: str):
    """Build a Drive service directly from a refresh token (no DB lookup)."""
    from google.oauth2.credentials import Credentials  # type: ignore
    from google.auth.transport.requests import Request  # type: ignore
    from googleapiclient.discovery import build  # type: ignore

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=_GOOGLE_TOKEN_URI,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=_DRIVE_SCOPES,
    )
    creds.refresh(Request())
    return build("drive", "v3", credentials=creds), creds


def auto_restore_on_startup() -> bool:
    """
    Called at app startup. If GOOGLE_DRIVE_REFRESH_TOKEN is set in env AND the
    local DB has never connected to Drive (fresh machine), downloads the latest
    backup from Drive and restores it synchronously.

    Returns True if a restore was performed.
    """
    from app.core.config import settings as _s
    from app.core.database import engine
    from app.api.backup import _do_restore

    refresh_token = _s.GOOGLE_DRIVE_REFRESH_TOKEN.strip()
    if not refresh_token:
        return False

    # Check if Drive is already configured locally (token in DB)
    try:
        from sqlmodel import Session as _Session
        with _Session(engine) as session:
            d = session.get(DriveSettings, 1)
            if d and d.refresh_token:
                logger.info("[auto-restore] Drive already configured locally, skipping.")
                return False
    except Exception:
        pass  # tables may not exist yet

    logger.info("[auto-restore] Fresh machine detected + GOOGLE_DRIVE_REFRESH_TOKEN set. Fetching latest Drive backup…")

    try:
        service, creds = _build_drive_service_from_token(refresh_token)
        folder_id = _get_folder_by_path(service, _FOLDER_PATH)

        results = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            orderBy="modifiedTime desc",
            fields="files(id, name, size, modifiedTime)",
            pageSize=5,
        ).execute()
        files = results.get("files", [])
        if not files:
            logger.warning("[auto-restore] No backup files found on Drive.")
            return False

        latest = files[0]
        logger.info("[auto-restore] Restoring from: %s", latest["name"])

        from googleapiclient.http import MediaIoBaseDownload  # type: ignore
        request = service.files().get_media(fileId=latest["id"])
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        content = buf.getvalue()

        if not content:
            logger.error("[auto-restore] Downloaded file is empty.")
            return False

        # Restore synchronously (blocking) so the app is ready when startup finishes
        job_id = "startup-restore"
        from app.api.backup import _jobs
        _jobs[job_id] = {"progress": 0, "status": "running", "message": "Starting…"}
        _do_restore(content, latest["name"], job_id)

        result = _jobs.get(job_id, {})
        if result.get("status") == "done":
            logger.info("[auto-restore] Restore complete: %s", result.get("message"))
            # Save refresh token to DB so Drive works normally going forward
            try:
                from sqlmodel import Session as _Session
                with _Session(engine) as session:
                    d = session.get(DriveSettings, 1) or DriveSettings(id=1)
                    d.refresh_token = refresh_token
                    d.access_token = creds.token
                    d.token_expiry = creds.expiry
                    session.add(d)
                    session.commit()
            except Exception as e:
                logger.warning("[auto-restore] Could not save token to DB: %s", e)
            return True
        else:
            logger.error("[auto-restore] Restore failed: %s", result.get("message"))
            return False

    except Exception as e:
        logger.error("[auto-restore] Error: %s", e, exc_info=True)
        return False
