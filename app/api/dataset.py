import csv
import io
import subprocess
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import get_current_user

router = APIRouter()

DATASET_DIR = Path("/app/dataset")
AUDIO_DIR = DATASET_DIR / "audio"
METADATA_CSV = DATASET_DIR / "metadata.csv"

CSV_HEADER = ["id", "audio_file", "transcription", "labels"]


def _ensure_dirs() -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)


# ── Save audio file only (no metadata yet) ───────────────────────────────────
@router.post("/save-audio")
async def save_audio(
    audio: UploadFile = File(...),
    audio_id: str = Form(...),
    _=Depends(get_current_user),
):
    _ensure_dirs()

    audio_bytes = await audio.read()
    webm_path = AUDIO_DIR / f"{audio_id}.webm"
    wav_path = AUDIO_DIR / f"{audio_id}.wav"

    webm_path.write_bytes(audio_bytes)

    # Convert webm → wav using ffmpeg (available in the container)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(webm_path), str(wav_path)],
            capture_output=True,
            check=True,
        )
        webm_path.unlink(missing_ok=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        if webm_path.exists():
            webm_path.rename(wav_path)

    return {"audio_id": audio_id, "saved": True}


# ── Save metadata rows (called at submit time with final customer names) ──────
class MetadataRecord(BaseModel):
    audio_id: str
    transcription: str
    labels: str = ""   # comma-separated "Name Amount" pairs e.g. "John Kumar 400, Mary 200"


@router.post("/save-metadata")
def save_metadata(
    records: List[MetadataRecord],
    _=Depends(get_current_user),
):
    if not records:
        return {"saved": 0}

    _ensure_dirs()

    # audio_id format: YYYYMMDD_HHMMSS — first 8 chars are the date
    date_prefix = records[0].audio_id[:8] if records[0].audio_id else None
    new_audio_ids = {rec.audio_id for rec in records}

    if date_prefix and len(date_prefix) == 8 and date_prefix.isdigit():
        # Remove old audio files for this date that aren't in the current batch
        for wav in AUDIO_DIR.glob(f"{date_prefix}*.wav"):
            if wav.stem not in new_audio_ids:
                wav.unlink(missing_ok=True)

        # Rebuild CSV: keep rows for other dates, replace rows for this date
        kept_rows: list[list[str]] = []
        if METADATA_CSV.exists():
            with open(METADATA_CSV, encoding="utf-8") as f:
                rows = list(csv.reader(f))
            if len(rows) > 1:
                kept_rows = [r for r in rows[1:] if r and not r[0].startswith(date_prefix)]

        with open(METADATA_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
            writer.writerows(kept_rows)
            for rec in records:
                writer.writerow([rec.audio_id, f"{rec.audio_id}.wav", rec.transcription, rec.labels])
    else:
        # Fallback: append as before
        write_header = not METADATA_CSV.exists()
        with open(METADATA_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(CSV_HEADER)
            for rec in records:
                writer.writerow([rec.audio_id, f"{rec.audio_id}.wav", rec.transcription, rec.labels])

    return {"saved": len(records)}


# ── Export dataset as zip (filtered to a specific date YYYYMMDD) ──────────────
@router.get("/export")
def export_dataset(date: str | None = None, _=Depends(get_current_user)):
    _ensure_dirs()

    # Use today if no date provided
    date_prefix = date or datetime.now().strftime("%Y%m%d")

    # Build CSV with only rows matching this date
    text_csv = io.StringIO()
    writer = csv.writer(text_csv)
    writer.writerow(CSV_HEADER)
    if METADATA_CSV.exists():
        with open(METADATA_CSV, encoding="utf-8") as f:
            rows = list(csv.reader(f))
        for row in rows[1:]:
            if row and row[0].startswith(date_prefix):
                writer.writerow(row)
    csv_bytes = text_csv.getvalue().encode("utf-8")
    del text_csv

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"dataset/metadata.csv", csv_bytes)
        for wav in sorted(AUDIO_DIR.glob(f"{date_prefix}*.wav")):
            zf.write(wav, f"dataset/audio/{wav.name}")

    buf.seek(0)
    filename = f"dataset_{date_prefix}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Clear entire dataset folder ───────────────────────────────────────────────
@router.delete("/clear")
def clear_dataset(_=Depends(get_current_user)):
    _ensure_dirs()
    deleted_audio = 0
    for wav in AUDIO_DIR.glob("*.wav"):
        wav.unlink(missing_ok=True)
        deleted_audio += 1
    for webm in AUDIO_DIR.glob("*.webm"):
        webm.unlink(missing_ok=True)
    if METADATA_CSV.exists():
        METADATA_CSV.unlink()
    return {"cleared": True, "audio_files_deleted": deleted_audio}


# ── Stats ─────────────────────────────────────────────────────────────────────
@router.get("/stats")
def dataset_stats(_=Depends(get_current_user)):
    _ensure_dirs()
    wav_count = len(list(AUDIO_DIR.glob("*.wav")))
    csv_rows = 0
    if METADATA_CSV.exists():
        with open(METADATA_CSV, encoding="utf-8") as f:
            csv_rows = max(0, sum(1 for _ in f) - 1)
    return {"audio_files": wav_count, "metadata_rows": csv_rows}
