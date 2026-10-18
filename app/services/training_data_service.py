"""
Training data -> Google Drive (My Drive / fintech / ocr|voice / <date> /).

NOTHING here touches the server's disk: the CSV is built in memory and every file is streamed to Drive from a BytesIO.
The browser sends the page image / the WAV recordings with the request; the server only relays them.
"""
from __future__ import annotations

import csv
import io
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlmodel import Session

from app.models.upi import DriveSettings
from app.schemas.training_data import CSV_COLUMNS, TrainingRow
from app.services import drive_service

log = logging.getLogger(__name__)

ROOT_FOLDER = "fintech"           # top level of My Drive — deliberately NOT inside gg_fin
IMAGE_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}
AUDIO_TYPES = {"wav": "audio/wav", "webm": "audio/webm", "ogg": "audio/ogg", "mp4": "audio/mp4", "m4a": "audio/mp4"}
_RESUMABLE_OVER = 5 * 1024 * 1024


class TrainingDataError(Exception):
    status_code = 500


class BadTrainingData(TrainingDataError):
    status_code = 400


class DriveNotConnected(TrainingDataError):
    status_code = 409


class DriveUploadFailed(TrainingDataError):
    status_code = 502


@dataclass
class Recording:
    id: str
    ext: str
    data: bytes
    transcription: str = ""


# ── pure helpers ──────────────────────────────────────────────────────────────

def format_amount(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def build_csv(rows: list[TrainingRow]) -> bytes:
    """The one CSV format shared by OCR and Voice (plain UTF-8, no BOM)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow([r.customer, r.date, format_amount(r.amount), r.type, r.mode, r.raw_text, r.source_file, r.origin])
    return buf.getvalue().encode("utf-8")


def new_stamp(now: datetime | None = None) -> str:
    """Unique per submit, so two submits in the same second can never overwrite each other."""
    now = now or datetime.now(timezone.utc)
    return f"{now:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:4]}"


def folder_date_for(rows: list[TrainingRow], requested: str | None = None) -> str:
    """The date folder: what the client asked for (yyyy-mm-dd), else the earliest date in the rows, else today."""
    if requested:
        try:
            return datetime.strptime(requested.strip(), "%Y-%m-%d").date().isoformat()
        except ValueError:
            pass
    dates = sorted(r.date for r in rows)
    return dates[0] if dates else datetime.now(timezone.utc).date().isoformat()


# ── Drive ─────────────────────────────────────────────────────────────────────

def _drive(session: Session):
    d = session.get(DriveSettings, 1)
    if not d or not d.access_token:
        raise DriveNotConnected("Google Drive is not connected — connect it in Settings to save training data.")
    try:
        return drive_service._build_drive_service(d, session)
    except RuntimeError as exc:                      # token refresh failed / library missing
        raise DriveNotConnected(str(exc)) from exc


def _upload(service, folder_id: str, name: str, data: bytes, mimetype: str) -> str:
    from googleapiclient.http import MediaIoBaseUpload  # type: ignore  (in-memory; never MediaFileUpload)

    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mimetype, resumable=len(data) > _RESUMABLE_OVER)
    created = service.files().create(
        body={"name": name, "parents": [folder_id]}, media_body=media, fields="id,name",
    ).execute()
    return created.get("name", name)


def _folder(service, kind: str, day: str) -> str:
    return drive_service._get_folder_by_path(service, [ROOT_FOLDER, kind, day])


# ── OCR: one page image + its summary table ───────────────────────────────────

def save_ocr_page(session: Session, *, image: bytes, image_ext: str, page: int, rows: list[TrainingRow],
                  folder_date: str | None = None) -> dict:
    if not rows:
        raise BadTrainingData("No rows to save.")
    if image_ext not in IMAGE_TYPES:
        raise BadTrainingData(f"Unsupported image type '{image_ext}'.")
    if not image:
        raise BadTrainingData("The page image is empty.")

    day = folder_date_for(rows, folder_date)
    base = f"{new_stamp()}_p{max(page, 1):03d}"
    image_name, csv_name = f"{base}.{image_ext}", f"{base}.csv"
    final_rows = [r.model_copy(update={"source_file": image_name}) for r in rows]

    service = _drive(session)
    try:
        folder_id = _folder(service, "ocr", day)
        _upload(service, folder_id, image_name, image, IMAGE_TYPES[image_ext])     # image first: a CSV never points at a missing file
        _upload(service, folder_id, csv_name, build_csv(final_rows), "text/csv")
    except TrainingDataError:
        raise
    except Exception as exc:
        log.warning("Training data (OCR) upload failed: %s", exc)
        raise DriveUploadFailed(f"Google Drive upload failed: {exc}") from exc
    return {"saved": len(final_rows), "folder": f"{ROOT_FOLDER}/ocr/{day}", "files": [image_name, csv_name]}


# ── Voice: recordings (+ full transcription) and one table ────────────────────

def save_voice_batch(session: Session, *, recordings: list[Recording], rows: list[TrainingRow],
                     folder_date: str | None = None) -> dict:
    if not rows:
        raise BadTrainingData("No rows to save.")
    if not recordings:
        raise BadTrainingData("No recordings to save.")
    known = {rec.id for rec in recordings}
    unknown = sorted({r.source_file for r in rows if r.source_file not in known})
    if unknown:
        raise BadTrainingData(f"Rows refer to recordings that were not uploaded: {', '.join(unknown)}")
    for rec in recordings:
        if rec.ext not in AUDIO_TYPES:
            raise BadTrainingData(f"Unsupported audio type '{rec.ext}'.")
        if not rec.data:
            raise BadTrainingData(f"Recording '{rec.id}' is empty.")

    day = folder_date_for(rows, folder_date)
    stamp = new_stamp()
    names = {rec.id: f"{stamp}_rec{i:02d}.{rec.ext}" for i, rec in enumerate(recordings, start=1)}
    final_rows = [r.model_copy(update={"source_file": names[r.source_file]}) for r in rows]
    csv_name = f"{stamp}.csv"

    service = _drive(session)
    uploaded: list[str] = []
    try:
        folder_id = _folder(service, "voice", day)
        for rec in recordings:
            audio_name = names[rec.id]
            uploaded.append(_upload(service, folder_id, audio_name, rec.data, AUDIO_TYPES[rec.ext]))
            if rec.transcription.strip():                                         # the full raw transcription next to its audio
                txt_name = audio_name.rsplit(".", 1)[0] + ".txt"
                uploaded.append(_upload(service, folder_id, txt_name, rec.transcription.encode("utf-8"), "text/plain"))
        uploaded.append(_upload(service, folder_id, csv_name, build_csv(final_rows), "text/csv"))
    except TrainingDataError:
        raise
    except Exception as exc:
        log.warning("Training data (voice) upload failed: %s", exc)
        raise DriveUploadFailed(f"Google Drive upload failed: {exc}") from exc
    return {"saved": len(final_rows), "folder": f"{ROOT_FOLDER}/voice/{day}", "files": uploaded}
