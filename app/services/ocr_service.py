"""
OCR service — PDF session management, page preprocessing, and Gemini extraction.

Pipeline per page:
    PDF → OCRPreprocessor (resize + grayscale + CLAHE + denoise + sharpen → JPEG)
        → cached on disk per session → Gemini Vision API

Preprocessing is done on-demand and cached, so the first `get_page_image_b64`
call for a page runs the preprocessor and writes a JPEG; subsequent calls
(including `extract_page`) read from the cache instantly.

Graceful degradation: if OpenCV / the preprocessor package is unavailable, the
service falls back to PyMuPDF's raw PNG rendering so the rest of the app keeps
working.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz, process

log = logging.getLogger(__name__)

TEMP_DIR = Path(tempfile.gettempdir()) / "ocr_sessions"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# ── Extraction prompt ──────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """You are a specialized Tamil handwritten finance document extractor.

TASK: Extract all customer collection entries from this handwritten page image.

DOMAIN RULES:
- Products: EDI (default) or IOP (marked by EL/EN/E.L/E.N — interest payment marker)
- Payment: CASH (default) or ONLINE (marked by GG/G/GPAY/GB/GP/G.B — Google Pay)
- IGNORE these lines entirely:
  * Totals: மொத்தம், total, போய், கூட்டல்
  * Expenses: செலவு, expense
  * GPay summary boxes (grouping multiple transactions with a subtotal)
  * Settlement lines: தீர்வு
  * Standalone calculations like 3000+2000=5000
  * Lines with no customer name (pure numbers)
- DATE RULES:
  * Date appears at top of each section; applies to all following entries until the next date header
  * Common formats: DD-MM-YYYY, DD/MM/YY, DD-MonthName-YYYY (Tamil or English)
  * Tamil months → numbers: ஜனவரி=01, பிப்ரவரி=02, மார்ச்=03, ஏப்ரல்=04, மே=05, ஜூன்=06, ஜூலை=07, ஆகஸ்ட்=08, செப்டம்பர்=09, அக்டோபர்=10, நவம்பர்=11, டிசம்பர்=12
  * Normalize ALL dates to DD-MM-YYYY format
- AMOUNT RULES:
  * Right-aligned integers, typically 200–50000
  * If two amounts on line, take the FIRST one (second is usually running total)
  * OCR confusion: 1/l, 0/o, 5/S — use context to disambiguate
- NAME RULES:
  * Tamil script — transliterate phonetically to English
  * Appears at start of line before any markers
  * Use [UNCLEAR] if illegible
- LAYOUT: Some pages have TWO COLUMNS side by side — process each independently, left column first

OUTPUT: Return ONLY a valid JSON array with no markdown fences, no explanation.
Each element must have exactly these fields:
{"collection_date":"DD-MM-YYYY","customer_name":"transliterated name","product_type":"EDI","payment_mode":"CASH","online_marker":null,"amount":2000,"raw_text":"original line","confidence_score":0.95,"page_number":1,"notes":""}"""

# ── Preprocessor singleton ─────────────────────────────────────────────────────

_preprocessor: Optional[object] = None
_preprocessor_available: Optional[bool] = None  # None = not yet checked


def _get_preprocessor():
    """
    Return a lazily-initialised OCRPreprocessor tuned for Tamil finance documents,
    or None if OpenCV / the preprocessor package is not installed.
    """
    global _preprocessor, _preprocessor_available

    if _preprocessor_available is False:
        return None
    if _preprocessor is not None:
        return _preprocessor

    try:
        from app.ocr_preprocessor import OCRPreprocessor, PreprocessingConfig

        # Tuned for Tamil handwriting on financial record pages:
        #   - 160 DPI: Tamil character loops need slightly more detail than Latin
        #   - 1800 px max: keeps tile count low while preserving fine strokes
        #   - CLAHE clip 2.5: recovers faded ink on older paper
        #   - bilateral sigma 18: sharp ink edges, smooth paper grain
        #   - unsharp 0.45: crispens Tamil conjunct strokes and numeral serifs
        #   - JPEG 87: safer for closed-loop Tamil characters (ந, ல, ர, ழ…)
        config = PreprocessingConfig(
            render_dpi=160,
            max_long_side=1800,
            jpeg_quality=87,
            clahe_clip_limit=2.5,
            bilateral_sigma_color=18.0,
            bilateral_sigma_space=18.0,
            unsharp_strength=0.45,
            use_parallel_processing=False,  # single-page on-demand calls don't benefit
        )
        _preprocessor = OCRPreprocessor(config)
        _preprocessor_available = True
        log.info("OCR preprocessor initialised (OpenCV pipeline active)")
    except Exception as exc:
        _preprocessor_available = False
        log.warning("OCR preprocessor unavailable — falling back to raw PNG: %s", exc)

    return _preprocessor


# ── Session helpers ────────────────────────────────────────────────────────────

def _session_dir(session_id: str) -> Path:
    return TEMP_DIR / session_id


def _pdf_path(session_id: str) -> Path:
    return _session_dir(session_id) / "source.pdf"


def _cached_page_path(session_id: str, page_index: int) -> Path:
    return _session_dir(session_id) / f"page_{page_index:04d}.jpg"


# ── Public API ─────────────────────────────────────────────────────────────────

def save_pdf(file_bytes: bytes) -> tuple[str, int]:
    """Save uploaded PDF bytes and return (session_id, total_pages)."""
    import fitz

    session_id = str(uuid.uuid4())
    sdir = _session_dir(session_id)
    sdir.mkdir(parents=True, exist_ok=True)
    pdf = _pdf_path(session_id)
    pdf.write_bytes(file_bytes)
    with fitz.open(str(pdf)) as doc:
        total_pages = doc.page_count
    return session_id, total_pages


def get_page_image_b64(session_id: str, page_index: int) -> str:
    """
    Return base64-encoded image for display.
    Uses the preprocessed JPEG if available (preferred), raw PNG as fallback.
    """
    img_bytes, _ = _get_page_bytes(session_id, page_index)
    return base64.b64encode(img_bytes).decode()


_vertex_credentials: Optional[object] = None
_vertex_project: str = "gg-finance-2021"


def _get_vertex_credentials():
    """Load and cache the service account credentials (JSON parsing is done once)."""
    global _vertex_credentials, _vertex_project
    if _vertex_credentials is not None:
        return _vertex_credentials

    import json as _json
    from google.oauth2 import service_account

    sa_key_json = os.environ.get("GCP_SERVICE_ACCOUNT_KEY")
    if not sa_key_json:
        raise ValueError("GCP_SERVICE_ACCOUNT_KEY environment variable is not set")

    sa_info = _json.loads(sa_key_json)
    _vertex_project = sa_info.get("project_id", "gg-finance-2021")
    _vertex_credentials = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return _vertex_credentials


def _vertex_location_for(model: str) -> str:
    """
    Gemini 3.x preview models require the global Vertex AI endpoint.
    Stable/flash models run in the regional endpoint (default us-central1).
    """
    if model.startswith("gemini-3"):
        return "global"
    return os.environ.get("VERTEX_AI_LOCATION", "us-central1")


def extract_page(
    session_id: str, page_index: int, model: str = "gemini-2.5-flash"
) -> tuple[str, list[dict]]:
    """
    Preprocess the requested page and run Gemini vision extraction via Vertex AI.
    Returns (page_b64_for_display, list_of_extracted_records).
    """
    import vertexai
    from vertexai.generative_models import GenerativeModel, Part

    img_bytes, mime_type = _get_page_bytes(session_id, page_index)
    page_b64 = base64.b64encode(img_bytes).decode()

    credentials = _get_vertex_credentials()
    location = _vertex_location_for(model)
    vertexai.init(project=_vertex_project, location=location, credentials=credentials)
    log.info("Vertex AI: model=%s location=%s", model, location)

    gemini = GenerativeModel(model)
    response = gemini.generate_content([
        EXTRACTION_PROMPT,
        Part.from_data(data=img_bytes, mime_type=mime_type),
    ])

    text = response.text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    records = json.loads(text)
    return page_b64, records


def fuzzy_match(name: str, customers: list[dict], limit: int = 3) -> list[dict]:
    """Return top `limit` fuzzy matches from a {id, name} list."""
    if not customers or not name:
        return []
    names = [c["name"] for c in customers]
    matches = process.extract(name, names, scorer=fuzz.WRatio, limit=limit)
    return [
        {"id": customers[idx]["id"], "name": matched_name, "score": round(score / 100, 2)}
        for matched_name, score, idx in matches
    ]


# ── Internal helpers ───────────────────────────────────────────────────────────

def _get_page_bytes(session_id: str, page_index: int) -> tuple[bytes, str]:
    """
    Return (image_bytes, mime_type) for a page.

    Checks disk cache first.  If not cached:
      - Tries the OCR preprocessor → JPEG (preferred, 70–90% smaller)
      - Falls back to raw PyMuPDF PNG if OpenCV is unavailable

    Raises FileNotFoundError if the session doesn't exist.
    """
    sdir = _session_dir(session_id)
    if not sdir.exists():
        raise FileNotFoundError(f"OCR session not found or expired — please re-upload the PDF")

    cached = _cached_page_path(session_id, page_index)
    if cached.exists():
        return cached.read_bytes(), "image/jpeg"

    preprocessor = _get_preprocessor()
    if preprocessor is not None:
        return _preprocess_page(preprocessor, session_id, page_index, cached)
    else:
        return _render_page_png(session_id, page_index), "image/png"


def _preprocess_page(
    preprocessor, session_id: str, page_index: int, cache_path: Path
) -> tuple[bytes, str]:
    """Run the OCR preprocessor on a single page and cache the result."""
    pdf = _pdf_path(session_id)
    if not pdf.exists():
        raise FileNotFoundError("OCR session not found or expired — please re-upload the PDF")

    # process_pdf_to_memory with page_range=(n, n+1) processes exactly one page
    images, metrics = preprocessor.process_pdf_to_memory(
        pdf, page_range=(page_index, page_index + 1)
    )

    img_bytes = images[0] if images else None
    if not img_bytes:
        # Blank page or error — fall back to raw PNG so we still show something
        log.warning(
            "Page %d preprocessor returned None (blank/error) — falling back to PNG",
            page_index,
        )
        return _render_page_png(session_id, page_index), "image/png"

    pm = metrics.page_metrics[0] if metrics.page_metrics else None
    if pm:
        log.info(
            "Page %d preprocessed | %dx%d → %dx%d | %.1f%% smaller | ~%d tokens",
            page_index,
            pm.original_dimensions[0], pm.original_dimensions[1],
            pm.optimized_dimensions[0], pm.optimized_dimensions[1],
            pm.size_reduction_pct,
            pm.estimated_gemini_tokens_after,
        )

    cache_path.write_bytes(img_bytes)
    return img_bytes, "image/jpeg"


def _render_page_png(session_id: str, page_index: int) -> bytes:
    """Fallback: render a page via PyMuPDF at 2× zoom and return PNG bytes."""
    import fitz

    pdf = _pdf_path(session_id)
    if not pdf.exists():
        raise FileNotFoundError("OCR session not found or expired — please re-upload the PDF")

    with fitz.open(str(pdf)) as doc:
        page = doc[page_index]
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")

    return img_bytes
