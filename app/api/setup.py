"""First-run setup endpoints — no authentication required."""
from fastapi import APIRouter, Depends
from sqlmodel import Session, select, func

from app.core.database import get_session
from app.models.customer import EdiCustomer, IopCustomer
from app.models.upi import DriveSettings

router = APIRouter()


def _is_fresh(session: Session) -> bool:
    """True when DB has no customer data AND Drive has never been connected locally."""
    edi_count = session.exec(select(func.count()).select_from(EdiCustomer)).one()  # type: ignore
    iop_count = session.exec(select(func.count()).select_from(IopCustomer)).one()  # type: ignore
    drive = session.get(DriveSettings, 1)
    has_drive_token = bool(drive and drive.refresh_token)
    return int(edi_count) == 0 and int(iop_count) == 0 and not has_drive_token


@router.get("/status")
def setup_status(session: Session = Depends(get_session)):
    """Return whether this is a fresh (empty) installation."""
    return {"is_fresh": _is_fresh(session)}


@router.get("/drive-status")
def setup_drive_status(session: Session = Depends(get_session)):
    """Return Drive connection status — no auth required (used during first-run setup)."""
    drive = session.get(DriveSettings, 1)
    connected = bool(drive and drive.refresh_token)
    email = drive.email if drive else None
    return {"connected": connected, "email": email}


@router.get("/drive-auth-url")
def setup_drive_auth_url():
    """Return Google OAuth URL for Drive — no auth required (used during first-run setup)."""
    from app.services.drive_service import get_auth_url
    try:
        return {"url": get_auth_url()}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/restore-latest")
def setup_restore_latest(session: Session = Depends(get_session)):
    """
    Restore the latest Drive backup. Called from the setup UI after Drive is connected.
    No auth required — only meaningful when DB is fresh.
    """
    from app.services import drive_service
    from app.api.backup import _jobs, _do_restore
    import io, uuid
    from googleapiclient.http import MediaIoBaseDownload  # type: ignore

    d = session.get(DriveSettings, 1)
    if not d or not d.refresh_token:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Google Drive not connected.")

    service = drive_service._build_drive_service(d, session)
    folder_id = drive_service._get_folder_by_path(service, drive_service._FOLDER_PATH)

    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        orderBy="modifiedTime desc",
        fields="files(id, name)",
        pageSize=5,
    ).execute()
    files = results.get("files", [])
    if not files:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="No backups found in Google Drive.")

    latest = files[0]
    request = service.files().get_media(fileId=latest["id"])
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    content = buf.getvalue()

    import threading
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"progress": 5, "status": "running", "message": "Downloaded from Drive, restoring…"}
    t = threading.Thread(target=_do_restore, args=(content, latest["name"], job_id), daemon=True)
    t.start()
    return {"job_id": job_id, "file_name": latest["name"]}


@router.get("/restore-status/{job_id}")
def setup_restore_status(job_id: str):
    """Poll restore job progress — no auth required."""
    from app.api.backup import _jobs
    job = _jobs.get(job_id)
    if not job:
        return {"progress": 0, "status": "unknown", "message": "Job not found"}
    return job
