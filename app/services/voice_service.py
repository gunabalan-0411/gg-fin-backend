from __future__ import annotations
import tempfile
import os
from datetime import date
from pathlib import Path

from sqlmodel import Session, select, col

from app.models.customer import EdiCustomer, IopCustomer
from app.models.mapping import EdiNameMap, IopNameMap
from app.utils.name_matching import get_similar_score, parse_voice_entry, parse_online_entry, detect_online_names


AUTO_MATCH_THRESHOLD = 90  # score >= this → auto-matched

# Local model directory (populated by download_model.py)
_LOCAL_MODEL_DIR = Path("/app/models/whisper-small")

# Module-level singleton — loaded once, shared across all requests
_whisper_model = None
_current_device: str = "cpu"  # "cpu" or "cuda"


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
    print(f"[whisper] Device switched to {device!r} — model will reload on next transcription")


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        if _LOCAL_MODEL_DIR.exists() and any(_LOCAL_MODEL_DIR.iterdir()):
            model_path = str(_LOCAL_MODEL_DIR)
            print(f"[whisper] Loading model from local path: {model_path}")
        else:
            model_path = "small"
            print("[whisper] Local model not found — downloading 'small' from HuggingFace")
        compute = "float16" if _current_device == "cuda" else "int8"
        print(f"[whisper] Loading model on device={_current_device!r} compute_type={compute!r}")
        _whisper_model = WhisperModel(model_path, device=_current_device, compute_type=compute)
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
        try:
            segments, info = model.transcribe(
                tmp_path,
                beam_size=5,
                language=lang,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
                without_timestamps=True,
            )
            texts = [s.text.strip() for s in segments]
            print(f"[whisper] product={product} lang={lang} detected={info.language} prob={info.language_probability:.2f} segments={texts}")
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
                    select(EdiCustomer).where(col(EdiCustomer.outstanding_balance) > 0)
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
                    select(IopCustomer).where(col(IopCustomer.loan_closure) > 0)
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
