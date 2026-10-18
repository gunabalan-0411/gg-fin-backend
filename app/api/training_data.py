"""
Training data (OCR page + table, Voice recordings + table) -> Google Drive.

The browser sends the files; this API relays them to Drive from memory. Nothing is ever written to the server's disk.
Callers treat a failure as non-fatal (the records themselves are saved through their own endpoints first).
"""
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import TypeAdapter, ValidationError
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session
from app.schemas.training_data import TrainingRow
from app.services import training_data_service as svc
from app.services.training_data_service import Recording, TrainingDataError

router = APIRouter()

_ROWS = TypeAdapter(list[TrainingRow])
MAX_ROWS = 2000
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_AUDIO_BYTES = 30 * 1024 * 1024
MAX_RECORDINGS = 40


def _parse_rows(raw: str) -> list[TrainingRow]:
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "rows must be a JSON list")
    if not isinstance(data, list):
        raise HTTPException(400, "rows must be a JSON list")
    if len(data) > MAX_ROWS:
        raise HTTPException(413, f"At most {MAX_ROWS} rows per upload")
    try:
        return _ROWS.validate_python(data)
    except ValidationError as exc:
        raise HTTPException(422, [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()])


def _ext(filename: str | None) -> str:
    name = filename or ""
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


async def _run(fn, *args, **kwargs):
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except TrainingDataError as exc:
        raise HTTPException(exc.status_code, str(exc))


@router.post("/ocr")
async def save_ocr(
    image: UploadFile = File(...),
    rows: str = Form(...),
    page: int = Form(1),
    folder_date: str | None = Form(None),
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """One OCR page: the page image as shown on screen + the submitted summary table."""
    parsed = _parse_rows(rows)
    data = await image.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Page image is too large")
    ext = _ext(image.filename)
    if ext not in svc.IMAGE_TYPES:                                    # fall back to the declared content type
        ext = {"image/jpeg": "jpg", "image/png": "png"}.get(image.content_type or "", ext)
    return await _run(svc.save_ocr_page, session, image=data, image_ext=ext, page=page, rows=parsed, folder_date=folder_date)


@router.post("/voice")
async def save_voice(
    recordings: list[UploadFile] = File(...),
    rows: str = Form(...),
    transcriptions: str = Form("{}"),
    folder_date: str | None = Form(None),
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """
    A voice submit: every recording (file name = its id, e.g. `rec1.wav`) + the full transcription of each
    (JSON object {id: text}) + the summary table whose `source_file` is a recording id.
    """
    parsed = _parse_rows(rows)
    if len(recordings) > MAX_RECORDINGS:
        raise HTTPException(413, f"At most {MAX_RECORDINGS} recordings per upload")
    try:
        texts = json.loads(transcriptions or "{}")
        if not isinstance(texts, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "transcriptions must be a JSON object {recording id: text}")

    items: list[Recording] = []
    for f in recordings:
        data = await f.read()
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "A recording is too large")
        rec_id = (f.filename or "").rsplit(".", 1)[0]
        items.append(Recording(id=rec_id, ext=_ext(f.filename), data=data, transcription=str(texts.get(rec_id, ""))))
    if len({r.id for r in items}) != len(items):
        raise HTTPException(400, "Recording file names must be unique")
    return await _run(svc.save_voice_batch, session, recordings=items, rows=parsed, folder_date=folder_date)
