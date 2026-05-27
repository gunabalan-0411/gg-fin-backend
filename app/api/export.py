"""Server-side PDF export — uses MuPDF (fitz.Story) with HarfBuzz for proper Tamil shaping.

The browser just fetches a URL and receives a ready PDF file.
No html2canvas, no jsPDF, zero main-thread blocking.
"""
import io
import logging
import os
import traceback

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session

log = logging.getLogger(__name__)

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.customer import EdiCustomer, IopCustomer
from app.models.mapping import EdiNameMap, IopNameMap
from app.services.transaction_service import EdiTransactionService, IopTransactionService

router = APIRouter()

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "fonts")
_TAMIL_FONT_FILE = "NotoSansTamil-Regular.ttf"
_TAMIL_FONT_PATH = os.path.join(_FONTS_DIR, _TAMIL_FONT_FILE)

_LABELS = {
    "ta": {
        "loan_start":  "கடன் தொடக்கம்",
        "loan_amount": "கடன் தொகை",
        "total_paid":  "மொத்த செலுத்தல்",
        "outstanding": "நிலுவை தொகை",
        "date":        "தேதி",
        "amount":      "தொகை",
        "mode":        "செலுத்து முறை",
        "status":      "நிலை",
        "paid":        "செலுத்தப்பட்டது",
        "unpaid":      "நிலுவை",
        "modes":       {"cash": "பணம்", "online": "ஆன்லைன்", "gpay": "கூகுள் பே", "upi": "யுபிஐ"},
    },
    "en": {
        "loan_start":  "Loan Start",
        "loan_amount": "Loan Amount",
        "total_paid":  "Total Paid",
        "outstanding": "Outstanding",
        "date":        "Date",
        "amount":      "Amount",
        "mode":        "Mode",
        "status":      "Status",
        "paid":        "PAID",
        "unpaid":      "UNPAID",
        "modes":       {},
    },
}


def _fmt_date(d) -> str:
    if not d:
        return "—"
    parts = str(d).split("-")
    return f"{parts[2]}-{parts[1]}-{parts[0]}" if len(parts) == 3 else str(d)


def _fmt_amt(n: float) -> str:
    # Indian-style comma formatting: 1,00,000
    n = int(round(n))
    s = str(abs(n))
    if len(s) > 3:
        s = s[:-3] + "," + s[-3:]
    pos = len(s) - 6
    while pos > 0:
        s = s[:pos] + "," + s[pos:]
        pos -= 2
    return ("−" if n < 0 else "") + "₹" + s


@router.get("/{product}/{customer_id}/export.pdf")
def export_customer_pdf(
    product: str,
    customer_id: int,
    lang: str = "en",
    filter: str = "all",
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    import fitz  # PyMuPDF — lazy import keeps startup fast

    # ── Fetch data ────────────────────────────────────────────────────────────
    if product == "edi":
        customer = session.get(EdiCustomer, customer_id)
        txns = EdiTransactionService(session).list_by_customer(customer_id)
        name_map = session.get(EdiNameMap, customer_id)
    elif product == "iop":
        customer = session.get(IopCustomer, customer_id)
        txns = IopTransactionService(session).list_by_customer(customer_id)
        name_map = session.get(IopNameMap, customer_id)
    else:
        raise HTTPException(400, "product must be 'edi' or 'iop'")

    if not customer:
        raise HTTPException(404, "Customer not found")

    tamil_name = (name_map.customer_name_ta or "") if name_map else ""

    L = _LABELS.get(lang, _LABELS["en"])

    # ── Compute totals ────────────────────────────────────────────────────────
    paid_txns   = [t for t in txns if t.payment_status == "PAID"]
    total_paid  = sum(float(t.amount) for t in paid_txns)
    loan_amount = float(customer.loan_amount or 0)
    outstanding = loan_amount - total_paid

    show_txns = sorted(
        paid_txns if filter == "paid" else list(txns),
        key=lambda t: str(t.collection_date or ""),
    )

    # ── Names ─────────────────────────────────────────────────────────────────
    is_tamil  = lang == "ta" and bool(tamil_name)
    title     = tamil_name if is_tamil else (customer.customer_name or "Customer")
    subtitle  = customer.customer_name if is_tamil else None

    # ── Summary cards ─────────────────────────────────────────────────────────
    cards = [
        (L["loan_start"],  _fmt_date(customer.loan_start_date)),
        (L["loan_amount"], _fmt_amt(loan_amount)),
        (L["total_paid"],  _fmt_amt(total_paid)),
    ]
    if product == "edi":
        cards.append((L["outstanding"], _fmt_amt(outstanding)))

    col_pct = 100 // len(cards)
    cards_html = "".join(
        f'<td style="background:#f3f4f6;border-radius:6px;padding:10px 12px;width:{col_pct}%">'
        f'<div style="font-size:10px;color:#6b7280;margin-bottom:3px">{lbl}</div>'
        f'<div style="font-size:13px;font-weight:bold">{val}</div>'
        f"</td>"
        for lbl, val in cards
    )

    # ── Transaction rows ──────────────────────────────────────────────────────
    rows_html = ""
    for i, t in enumerate(show_txns):
        bg     = "#ffffff" if i % 2 == 0 else "#f9fafb"
        status = L["paid"] if t.payment_status == "PAID" else L["unpaid"]
        sc     = "#059669" if t.payment_status == "PAID" else "#d97706"
        mode   = (t.payment_mode or "").lower()
        mlabel = L["modes"].get(mode) or (t.payment_mode or "").capitalize()
        rows_html += (
            f'<tr style="background:{bg}">'
            f'<td style="padding:7px 10px;border-bottom:1px solid #e5e7eb;font-size:12px">{_fmt_date(t.collection_date)}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #e5e7eb;font-size:12px">{_fmt_amt(float(t.amount))}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #e5e7eb;font-size:12px">{mlabel}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #e5e7eb;font-size:12px;color:{sc};font-weight:bold">{status}</td>'
            f"</tr>"
        )

    # ── Font setup ────────────────────────────────────────────────────────────
    has_font  = os.path.exists(_TAMIL_FONT_PATH)
    use_tamil = lang == "ta" and has_font

    font_css = (
        f'@font-face {{ font-family: "NotoTamil"; src: url("{_TAMIL_FONT_FILE}"); }}'
        if use_tamil else ""
    )
    body_font = '"NotoTamil", sans-serif' if use_tamil else "sans-serif"

    # ── HTML template ─────────────────────────────────────────────────────────
    n = len(show_txns)
    filter_note = f" ({L['paid']})" if filter == "paid" else ""
    footer = f"{n} {'transaction' if n == 1 else 'transactions'}{filter_note} · {product.upper()}"
    subtitle_html = (
        f'<p style="margin:0 0 14px;font-size:12px;color:#6b7280">{subtitle}</p>'
        if subtitle else '<div style="height:14px"></div>'
    )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
{font_css}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: {body_font};
  color: #111827;
  line-height: 1.45;
  font-size: 13px;
}}
h2 {{ font-size: 18px; margin-bottom: 4px; }}
table {{ border-collapse: collapse; width: 100%; }}
.summary {{ margin-bottom: 18px; border-spacing: 8px; border-collapse: separate; }}
.txn-head th {{
  background: #02B15A;
  color: #ffffff;
  padding: 9px 10px;
  text-align: left;
  font-size: 12px;
  font-weight: 600;
}}
.footer {{ margin-top: 10px; font-size: 10px; color: #9ca3af; }}
</style>
</head>
<body>
<h2>{title}</h2>
{subtitle_html}
<table class="summary"><tr>{cards_html}</tr></table>
<table>
  <thead><tr class="txn-head">
    <th>{L["date"]}</th>
    <th>{L["amount"]}</th>
    <th>{L["mode"]}</th>
    <th>{L["status"]}</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>
<p class="footer">{footer}</p>
</body></html>"""

    # ── Render via fitz.Story (MuPDF + HarfBuzz — correct Tamil ligature shaping) ──
    try:
        log.info("fitz version: %s", fitz.version)

        if use_tamil:
            archive = fitz.Archive(_FONTS_DIR)
        else:
            archive = None

        story = fitz.Story(html, archive=archive) if archive else fitz.Story(html)

        buf    = io.BytesIO()
        writer = fitz.DocumentWriter(buf)
        A4     = fitz.paper_rect("a4")   # 595 × 842 pt
        margin = 36
        clip   = fitz.Rect(A4.x0 + margin, A4.y0 + margin, A4.x1 - margin, A4.y1 - margin)

        more = True
        while more:
            device      = writer.begin_page(A4)
            more, _     = story.place(clip)
            story.draw(device)
            writer.end_page()

        writer.close()
    except Exception:
        log.error("PDF render failed:\n%s", traceback.format_exc())
        raise HTTPException(500, "PDF generation failed — check server logs")

    english_name = (customer.customer_name or "customer").replace(" ", "_")
    return Response(
        content=buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{english_name}_transactions.pdf"'},
    )
