from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.services import drive_service

router = APIRouter()


@router.get("/auth-url")
def drive_auth_url(_=Depends(get_current_user)):
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=400,
            detail="GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not configured in .env",
        )
    try:
        url = drive_service.get_auth_url()
        return {"url": url}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/oauth/callback")
def drive_callback(
    code: str = Query(...),
    session: Session = Depends(get_session),
):
    """Google redirects here after Drive consent. Public (no auth required)."""
    try:
        drive_service.exchange_code(code, session)
    except Exception as e:
        return RedirectResponse(
            url=f"http://localhost:3000/oauth-callback?type=drive&status=error&msg={str(e)[:80]}"
        )
    return RedirectResponse(url="http://localhost:3000/oauth-callback?type=drive&status=connected")


@router.get("/status")
def drive_status(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    return drive_service.get_status(session)


@router.delete("/disconnect")
def drive_disconnect(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    drive_service.disconnect(session)
    return {"ok": True}


@router.post("/export")
def drive_export(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """pg_dump → upload to Google Drive at gg_fin/db_bck_up."""
    try:
        result = drive_service.export_to_drive(session)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/files")
def drive_files(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """List backup files in the gg_fin/db_bck_up Drive folder."""
    try:
        files = drive_service.list_files(session)
        return {"data": files}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/import/status/{job_id}")
def drive_import_status(job_id: str):
    from app.api.backup import _jobs
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/import/{file_id}")
def drive_import(
    file_id: str,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """Download a backup from Drive and restore the database."""
    try:
        job_id = drive_service.start_import_job(file_id, session)
        return {"job_id": job_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/refresh-token")
def get_refresh_token(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """Return the stored Drive refresh token so the user can copy it to .env on a new machine."""
    from app.models.upi import DriveSettings
    d = session.get(DriveSettings, 1)
    if not d or not d.refresh_token:
        raise HTTPException(status_code=404, detail="Google Drive not connected.")
    return {"refresh_token": d.refresh_token}
