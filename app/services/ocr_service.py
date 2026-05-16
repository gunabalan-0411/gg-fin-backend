import base64
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

from rapidfuzz import fuzz, process

TEMP_DIR = Path(tempfile.gettempdir()) / "ocr_sessions"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

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


def save_pdf(file_bytes: bytes) -> tuple[str, int]:
    import fitz  # lazy — keeps startup safe if PyMuPDF install is broken
    session_id = str(uuid.uuid4())
    pdf_path = TEMP_DIR / f"{session_id}.pdf"
    pdf_path.write_bytes(file_bytes)
    with fitz.open(str(pdf_path)) as doc:
        total_pages = doc.page_count
    return session_id, total_pages


def get_page_image_b64(session_id: str, page_index: int) -> str:
    import fitz
    pdf_path = TEMP_DIR / f"{session_id}.pdf"
    if not pdf_path.exists():
        raise FileNotFoundError(f"Session {session_id} not found or expired")
    with fitz.open(str(pdf_path)) as doc:
        page = doc[page_index]
        mat = fitz.Matrix(2.0, 2.0)  # 2× zoom for sharper OCR
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
    return base64.b64encode(img_bytes).decode()


def extract_page(session_id: str, page_index: int) -> tuple[str, list[dict]]:
    from google import genai
    from google.genai import types

    page_b64 = get_page_image_b64(session_id, page_index)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is not set")

    client = genai.Client(api_key=api_key)
    img_data = base64.b64decode(page_b64)

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[
            EXTRACTION_PROMPT,
            types.Part.from_bytes(data=img_data, mime_type="image/png"),
        ],
    )
    text = response.text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    records = json.loads(text)
    return page_b64, records


def fuzzy_match(name: str, customers: list[dict], limit: int = 3) -> list[dict]:
    if not customers or not name:
        return []
    names = [c["name"] for c in customers]
    matches = process.extract(name, names, scorer=fuzz.WRatio, limit=limit)
    return [
        {"id": customers[idx]["id"], "name": matched_name, "score": round(score / 100, 2)}
        for matched_name, score, idx in matches
    ]
