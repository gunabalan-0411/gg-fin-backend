from __future__ import annotations
import logging
import tempfile
import os
import threading
import time
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

from sqlmodel import Session, select, col

from app.models.customer import EdiCustomer, IopCustomer
from app.models.mapping import EdiNameMap, IopNameMap
from app.utils.name_matching import get_similar_score, parse_voice_entry, parse_online_entry, detect_online_names


AUTO_MATCH_THRESHOLD = 90  # score >= this → auto-matched
_IDLE_UNLOAD_SECONDS = 300  # unload model after 5 min of inactivity

_MODEL_SIZE = "small"  # faster-whisper resolves this to its own HF repo internally
_DRIVE_MODEL_FOLDER = ["gg_fin", "voice_model"]  # My Drive → gg_fin → voice_model

# Local on-disk cache for the currently running container — ephemeral (no Railway
# volume needed); Google Drive is now the persistent store across deploys/restarts.
_LOCAL_MODEL_DIR = Path("/app/model_cache")

# Module-level singleton — loaded on demand, auto-unloaded after idle
_whisper_model = None
_current_device: str = "cpu"  # "cpu" or "cuda"
_last_used: float = 0.0
_unload_timer: threading.Timer | None = None
_timer_lock = threading.Lock()

# Download state
_download_in_progress: bool = False
_download_lock = threading.Lock()
_EXPECTED_MODEL_BYTES = 490_000_000  # ~480 MB for faster-whisper-small
_download_progress: float = 0.0  # 0.0–1.0


def _schedule_unload() -> None:
    global _unload_timer
    with _timer_lock:
        if _unload_timer is not None:
            _unload_timer.cancel()
        t = threading.Timer(_IDLE_UNLOAD_SECONDS, _auto_unload)
        t.daemon = True
        t.start()
        _unload_timer = t


def _auto_unload() -> None:
    global _whisper_model, _unload_timer
    with _timer_lock:
        _whisper_model = None
        _unload_timer = None
    log.info("[whisper] Auto-unloaded after 60s idle")


def _is_model_on_disk() -> bool:
    return (_LOCAL_MODEL_DIR / "model.bin").exists()


def _get_drive_service_for_model():
    """Build an authenticated Drive client using the existing DriveSettings tokens
    (the same Google account already connected for DB backups in Settings)."""
    from sqlmodel import Session as _Session
    from app.core.database import engine
    from app.models.upi import DriveSettings
    from app.services.drive_service import _build_drive_service

    with _Session(engine) as session:
        d = session.get(DriveSettings, 1)
        if not d or not d.refresh_token:
            raise RuntimeError("Google Drive not connected — connect Drive in Settings first.")
        return _build_drive_service(d, session)


def _download_model_from_drive() -> bool:
    """Download model files from Drive's gg_fin/voice_model folder. Returns False if not present there."""
    from app.services.drive_service import _get_folder_by_path
    from googleapiclient.http import MediaIoBaseDownload  # type: ignore

    service = _get_drive_service_for_model()
    folder_id = _get_folder_by_path(service, _DRIVE_MODEL_FOLDER)

    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id, name, size)",
        pageSize=20,
    ).execute()
    files = results.get("files", [])
    if not any(f["name"] == "model.bin" for f in files):
        return False

    _LOCAL_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for f in files:
        dest = _LOCAL_MODEL_DIR / f["name"]
        request = service.files().get_media(fileId=f["id"])
        with open(dest, "wb") as out:
            downloader = MediaIoBaseDownload(out, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        log.info(f"[whisper] Downloaded {f['name']} from Drive (gg_fin/voice_model)")
    return True


def _upload_model_to_drive() -> None:
    """Upload the locally-cached model files to Drive's gg_fin/voice_model folder."""
    from app.services.drive_service import _get_folder_by_path
    from googleapiclient.http import MediaFileUpload  # type: ignore

    service = _get_drive_service_for_model()
    folder_id = _get_folder_by_path(service, _DRIVE_MODEL_FOLDER)

    existing = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(name)", pageSize=20,
    ).execute().get("files", [])
    existing_names = {f["name"] for f in existing}

    for f in _LOCAL_MODEL_DIR.iterdir():
        if not f.is_file() or f.name in existing_names:
            continue
        media = MediaFileUpload(str(f), resumable=True)
        service.files().create(
            body={"name": f.name, "parents": [folder_id]},
            media_body=media,
            fields="id",
        ).execute()
        log.info(f"[whisper] Uploaded {f.name} to Drive (gg_fin/voice_model)")


def _flatten_hf_snapshot(snapshot_dir: str) -> None:
    """Copy the resolved HF snapshot files into the flat _LOCAL_MODEL_DIR."""
    import shutil
    for f in Path(snapshot_dir).iterdir():
        if f.is_file():
            shutil.copy2(f, _LOCAL_MODEL_DIR / f.name)


def _delete_model_from_drive() -> int:
    """Delete all files in Drive's gg_fin/voice_model folder. Returns count deleted."""
    from app.services.drive_service import _get_folder_by_path

    service = _get_drive_service_for_model()
    folder_id = _get_folder_by_path(service, _DRIVE_MODEL_FOLDER)
    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id, name)", pageSize=20,
    ).execute()
    files = results.get("files", [])
    for f in files:
        service.files().delete(fileId=f["id"]).execute()
        log.info(f"[whisper] Deleted {f['name']} from Drive (gg_fin/voice_model)")
    return len(files)


def reset_model_from_huggingface() -> dict:
    """User-confirmed recovery action: wipe the cached model (local disk + Drive),
    re-download a fresh copy from HuggingFace, and re-upload it to Drive."""
    global _whisper_model, _download_in_progress, _download_progress
    import shutil

    with _timer_lock:
        _whisper_model = None  # force unload of the (possibly broken) model

    with _download_lock:
        if _download_in_progress:
            raise RuntimeError("A model download is already in progress — try again shortly.")
        _download_in_progress = True
        _download_progress = 0.0

    deleted_from_drive = 0
    try:
        log.warning("[whisper] Resetting model — wiping local cache and Drive copy, re-fetching from HuggingFace…")
        if _LOCAL_MODEL_DIR.exists():
            shutil.rmtree(_LOCAL_MODEL_DIR, ignore_errors=True)
        _LOCAL_MODEL_DIR.mkdir(parents=True, exist_ok=True)

        try:
            deleted_from_drive = _delete_model_from_drive()
        except Exception as e:
            log.warning(f"[whisper] Could not clear Drive folder before reset: {e}")

        from faster_whisper.utils import download_model
        hf_cache = str(_LOCAL_MODEL_DIR / "_hf_cache")
        snapshot_dir = download_model(_MODEL_SIZE, cache_dir=hf_cache)
        _flatten_hf_snapshot(snapshot_dir)
        shutil.rmtree(hf_cache, ignore_errors=True)

        _upload_model_to_drive()
        _download_progress = 1.0
        log.info("[whisper] ✓ Model reset complete — fresh copy on disk and Drive")
    finally:
        _download_in_progress = False

    _get_whisper_model()
    return {"deleted_from_drive": deleted_from_drive, **get_model_status()}


def _ensure_model_on_disk() -> None:
    """Download model to volume if missing. Blocks caller; safe to call from multiple threads."""
    global _download_in_progress, _download_progress

    if _is_model_on_disk():
        return

    with _download_lock:
        # Re-check after acquiring lock — another thread may have finished downloading
        if _is_model_on_disk():
            return
        if _download_in_progress:
            # Wait for the in-progress download (started by startup background thread)
            while _download_in_progress:
                time.sleep(1)
            return

        _download_in_progress = True
        _download_progress = 0.0

    def _poll_progress() -> None:
        global _download_progress
        while _download_in_progress:
            try:
                if _LOCAL_MODEL_DIR.exists():
                    downloaded = sum(f.stat().st_size for f in _LOCAL_MODEL_DIR.rglob("*") if f.is_file())
                    _download_progress = min(downloaded / _EXPECTED_MODEL_BYTES, 0.95)
            except Exception:
                pass
            time.sleep(2)

    threading.Thread(target=_poll_progress, daemon=True).start()

    try:
        _LOCAL_MODEL_DIR.mkdir(parents=True, exist_ok=True)

        found_on_drive = False
        try:
            log.info("[whisper] Checking Google Drive (gg_fin/voice_model) for cached model…")
            found_on_drive = _download_model_from_drive()
        except Exception as e:
            log.warning(f"[whisper] Drive lookup/download failed, falling back to HuggingFace: {e}")

        if not found_on_drive:
            log.info("[whisper] Model not on Drive — downloading from HuggingFace…")
            from faster_whisper.utils import download_model
            hf_cache = str(_LOCAL_MODEL_DIR / "_hf_cache")
            snapshot_dir = download_model(_MODEL_SIZE, cache_dir=hf_cache)
            _flatten_hf_snapshot(snapshot_dir)
            import shutil
            shutil.rmtree(hf_cache, ignore_errors=True)
            try:
                log.info("[whisper] Uploading model to Drive (gg_fin/voice_model) for next time…")
                _upload_model_to_drive()
            except Exception as e:
                log.warning(f"[whisper] Could not upload model to Drive (will re-download from HF next restart): {e}")

        _download_progress = 1.0
        log.info("[whisper] ✓ Model ready on disk")
    except Exception as e:
        log.warning(f"[whisper] Download failed: {e}")
        raise
    finally:
        _download_in_progress = False


def start_background_download() -> None:
    """Call at app startup — if model is missing locally, fetch it (from Drive, or HF as fallback) then auto-load."""
    if _is_model_on_disk():
        log.info("[whisper] Model already on local disk — skipping background download")
        return

    def _bg():
        try:
            _ensure_model_on_disk()
            log.info("[whisper] Download complete — auto-loading into RAM…")
            _get_whisper_model()
            log.info("[whisper] Model ready in RAM")
        except Exception as e:
            log.warning(f"[whisper] Background download/load failed: {e}")

    t = threading.Thread(target=_bg, daemon=True)
    t.start()
    log.info("[whisper] Model missing locally — background fetch started")


def get_model_status() -> dict:
    loaded = _whisper_model is not None
    idle = int(time.time() - _last_used) if (loaded and _last_used) else 0
    return {
        "loaded": loaded,
        "on_disk": _is_model_on_disk(),
        "downloading": _download_in_progress,
        "download_progress": round(_download_progress * 100),
        "idle_seconds": idle,
        "seconds_until_unload": max(0, _IDLE_UNLOAD_SECONDS - idle) if loaded else 0,
    }


def load_model() -> dict:
    _ensure_model_on_disk()   # download first if volume is empty
    _get_whisper_model()
    return get_model_status()


def unload_model() -> dict:
    global _whisper_model, _unload_timer
    with _timer_lock:
        if _unload_timer is not None:
            _unload_timer.cancel()
            _unload_timer = None
        _whisper_model = None
    log.info("[whisper] Manually unloaded")
    return get_model_status()


def _is_cuda_available() -> bool:
    """Check CUDA via ctranslate2 (already installed with faster-whisper)."""
    try:
        import ctranslate2
        types = ctranslate2.get_supported_compute_types("cuda")
        return len(types) > 0
    except Exception:
        return False


def _get_gpu_name() -> str:
    """Get GPU name using nvidia-smi (available in CUDA Docker images)."""
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            name = result.stdout.strip().split("\n")[0]
            if name:
                return name
    except Exception:
        pass
    return "GPU"


def _get_cpu_name() -> str:
    try:
        import platform, re
        raw = platform.processor() or ""
        if not raw:
            # Linux: read /proc/cpuinfo
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        raw = line.split(":", 1)[1].strip()
                        break
        m = re.search(r"(Intel|AMD|Apple|ARM)[^\n]{0,50}", raw)
        return m.group(0).strip() if m else (raw[:40] or "CPU")
    except Exception:
        return "CPU"


def get_device_info() -> dict:
    """Return current device, device name, and whether GPU is available."""
    gpu_available = _is_cuda_available()
    device_name = _get_gpu_name() if (_current_device == "cuda" and gpu_available) else _get_cpu_name()
    return {
        "device": _current_device,
        "device_name": device_name,
        "gpu_available": gpu_available,
    }


def set_device(device: str) -> None:
    """Switch inference device; reloads Whisper model if the device changes."""
    global _whisper_model, _current_device
    if device not in ("cpu", "cuda"):
        raise ValueError(f"Unknown device: {device!r}")
    if not _is_cuda_available() and device == "cuda":
        raise ValueError("CUDA is not available on this machine")
    if device == _current_device:
        return
    _current_device = device
    _whisper_model = None  # force reload on next request
    log.info(f"[whisper] Device switched to {device!r} — model will reload on next transcription")


def _get_whisper_model():
    global _whisper_model, _last_used
    _last_used = time.time()
    if _whisper_model is None:
        _ensure_model_on_disk()  # blocks until model is on local disk (fetches from Drive/HF if needed)
        from faster_whisper import WhisperModel
        compute = "float16" if _current_device == "cuda" else "int8"
        log.info(f"[whisper] Loading model from {_LOCAL_MODEL_DIR} device={_current_device!r} compute={compute!r}")
        _whisper_model = WhisperModel(str(_LOCAL_MODEL_DIR), device=_current_device, compute_type=compute)
    _schedule_unload()
    return _whisper_model


class VoiceService:
    def __init__(self, session: Session):
        self.session = session

    def _get_whisper(self):
        return _get_whisper_model()

    def transcribe(self, audio_bytes: bytes, audio_suffix: str = ".webm", product: str = "edi") -> str:
        lang = "en"
        model = self._get_whisper()
        with tempfile.NamedTemporaryFile(suffix=audio_suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name
        log.info(f"[whisper] audio bytes={len(audio_bytes)} suffix={audio_suffix} file={tmp_path}")
        try:
            segments, info = model.transcribe(
                tmp_path,
                beam_size=5,
                language=lang,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=300),
                without_timestamps=True,
            )
            texts = [s.text.strip() for s in segments]
            log.info(f"[whisper] detected={info.language} prob={info.language_probability:.2f} segments={texts}")
            return " ".join(texts)
        finally:
            os.unlink(tmp_path)

    def _load_candidates(self, product: str) -> list[dict]:
        """
        Load customers with balance > 0 and their English names.
        Returns list of {customer_id, name (normalized), display_name, segment_id}
        """
        import re

        def norm(s: str) -> str:
            return re.sub(r"\s+", " ", (s or "").strip().lower())

        if product == "edi":
            active_ids = {
                c.customer_id
                for c in self.session.exec(
                    select(EdiCustomer).where(EdiCustomer.is_closed == False)  # noqa: E712
                ).all()
            }
            seg_map = {
                c.customer_id: c.customer_segment_id
                for c in self.session.exec(select(EdiCustomer)).all()
            }
            name_rows = self.session.exec(select(EdiNameMap)).all()
        else:
            active_ids = {
                c.customer_id
                for c in self.session.exec(
                    select(IopCustomer).where(IopCustomer.is_closed == False)  # noqa: E712
                ).all()
            }
            seg_map = {
                c.customer_id: c.customer_segment_id
                for c in self.session.exec(select(IopCustomer)).all()
            }
            name_rows = self.session.exec(select(IopNameMap)).all()

        candidates = []
        for row in name_rows:
            if row.customer_id not in active_ids:
                continue
            en = norm(row.customer_name_en or "")
            if not en:
                continue
            candidates.append({
                "customer_id": row.customer_id,
                "name": en,
                "display_name": row.customer_name_en or "",
                "segment_id": seg_map.get(row.customer_id),
            })
        return candidates

    @staticmethod
    def _is_tamil(text: str) -> bool:
        import re
        return bool(re.search(r"[\u0B80-\u0BFF]", text))

    def match_entries(self, transcription: str, product: str) -> list[dict]:
        """
        Returns list of match results. Each item:
        {
            spoken_name, amount, customer_id, customer_name,
            matched (bool), score (float),
            alternatives: [{customer_id, name, score}]  # top-3 when score < threshold
        }
        """
        entries = parse_voice_entry(transcription)
        candidates = self._load_candidates(product)
        results = []

        for entry in entries:
            spoken = entry["name"]
            amount = entry["amount"]

            if not candidates:
                results.append({
                    "spoken_name": spoken,
                    "amount": amount,
                    "customer_id": None,
                    "customer_name": None,
                    "matched": False,
                    "score": 0.0,
                    "alternatives": [],
                })
                continue

            scored = get_similar_score(spoken, candidates)
            top = scored[0] if scored else None
            top_score = top["score"] if top else 0.0

            if top and top_score >= AUTO_MATCH_THRESHOLD:
                results.append({
                    "spoken_name": spoken,
                    "amount": amount,
                    "customer_id": top["customer_id"],
                    "customer_name": top["name"],
                    "matched": True,
                    "score": top_score,
                    "alternatives": [],
                })
            else:
                # Return top 3 alternatives for user to choose
                alts = [
                    {"customer_id": s["customer_id"], "name": s["name"], "score": s["score"]}
                    for s in scored[:3]
                ]
                results.append({
                    "spoken_name": spoken,
                    "amount": amount,
                    "customer_id": top["customer_id"] if top else None,
                    "customer_name": top["name"] if top else None,
                    "matched": False,
                    "score": top_score,
                    "alternatives": alts,
                })

        return results

    def match_online_entries(self, transcription: str, product: str) -> list[dict]:
        """
        Match comma-separated names from the online-payer mic recording.
        Returns the same structure as match_entries but with amount=0.
        """
        names = parse_online_entry(transcription)
        candidates = self._load_candidates(product)
        results = []
        for name in names:
            if not candidates:
                results.append({
                    "spoken_name": name, "amount": 0,
                    "customer_id": None, "customer_name": None,
                    "matched": False, "score": 0.0, "alternatives": [],
                })
                continue
            scored = get_similar_score(name, candidates)
            top = scored[0] if scored else None
            top_score = top["score"] if top else 0.0
            if top and top_score >= AUTO_MATCH_THRESHOLD:
                results.append({
                    "spoken_name": name, "amount": 0,
                    "customer_id": top["customer_id"], "customer_name": top["name"],
                    "matched": True, "score": top_score, "alternatives": [],
                })
            else:
                alts = [{"customer_id": s["customer_id"], "name": s["name"], "score": s["score"]} for s in scored[:3]]
                results.append({
                    "spoken_name": name, "amount": 0,
                    "customer_id": top["customer_id"] if top else None,
                    "customer_name": top["name"] if top else None,
                    "matched": False, "score": top_score, "alternatives": alts,
                })
        return results

    def detect_online_payments(self, transcription: str, product: str) -> list[int]:
        """Return customer IDs whose names appear next to 'online' in the transcription."""
        spoken_names = detect_online_names(transcription)
        if not spoken_names:
            return []
        candidates = self._load_candidates(product)
        matched_ids: list[int] = []
        for name in spoken_names:
            scored = get_similar_score(name, candidates)
            if scored and scored[0]["score"] >= 60:
                cid = scored[0]["customer_id"]
                if cid not in matched_ids:
                    matched_ids.append(cid)
        return matched_ids
