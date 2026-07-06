"""Customer transaction PDF export — WeasyPrint renderer.

Design matches GG Finance TX Report reference exactly:
  - Header: brand + TRANSACTIONS badge left | label + customer name right
  - Summary strip: 4 bordered cells, outstanding highlighted dark
  - Transaction table: year-band separators, running balance, payment badges
  - Total row: dark background with total paid and final balance
  - Footer: customer + start date left | loan ID right
"""
import io
import logging
import pathlib
import traceback

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.customer import EdiCustomer, IopCustomer
from app.models.mapping import EdiNameMap, IopNameMap
from app.services.transaction_service import EdiTransactionService, IopTransactionService

router = APIRouter()
log = logging.getLogger(__name__)

_FONTS_DIR  = pathlib.Path(__file__).parent.parent.parent / "fonts"
_TAMIL_FONT = "NotoSansTamil-Regular.ttf"
_MONO_FONT  = "IBMPlexMono-Regular.ttf"


# ── Utilities ─────────────────────────────────────────────────────────────────

def _font_face_css() -> tuple[str, str, str]:
    t = _FONTS_DIR / _TAMIL_FONT
    m = _FONTS_DIR / _MONO_FONT
    face = ""
    if t.exists():
        face += f'@font-face {{font-family:"NotoTamil";src:url("{t.as_uri()}");font-weight:400 700;}}\n'
    if m.exists():
        face += f'@font-face {{font-family:"IBMPlexMono";src:url("{m.as_uri()}");font-weight:400 500 600;}}\n'
    body = '"NotoTamil", sans-serif' if t.exists() else "sans-serif"
    mono = '"IBMPlexMono", monospace' if m.exists() else "monospace"
    return face, body, mono


def _fmt_date(d) -> str:
    if not d:
        return "—"
    parts = str(d).split("-")
    return f"{parts[2]}-{parts[1]}-{parts[0]}" if len(parts) == 3 else str(d)


def _fmt_amt(n: float) -> str:
    n = int(round(n))
    s = str(abs(n))
    if len(s) > 3:
        s = s[:-3] + "," + s[-3:]
    pos = len(s) - 6
    while pos > 0:
        s = s[:pos] + "," + s[pos:]
        pos -= 2
    return ("−" if n < 0 else "") + "₹" + s


def _method_badge(mode: str) -> str:
    m = (mode or "").lower().strip()
    _online_labels = {"gpay": "GPay", "upi": "UPI", "phonepe": "PhonePe",
                      "neft": "NEFT", "imps": "IMPS", "online": "Online"}
    if m in _online_labels:
        return f'<span class="badge-online">{_online_labels[m]}</span>'
    return '<span class="badge-cash">பணம்</span>'


def _render_pdf(html: str) -> bytes:
    from weasyprint import HTML, CSS  # noqa
    page_css = CSS(string="@page { size: A4; margin: 0.65in 0.7in; }")
    buf = io.BytesIO()
    HTML(string=html, base_url=None).write_pdf(buf, stylesheets=[page_css])
    return buf.getvalue()


# ── CSS ───────────────────────────────────────────────────────────────────────

def _tx_css(font_face: str, body_font: str, mono_font: str) -> str:
    return f"""
{font_face}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: {body_font}; color: #1a1d23; font-size: 12px; line-height: 1.4; }}

/* ── Header ── */
.header-top {{
  display: flex; align-items: flex-start; justify-content: space-between;
  padding-bottom: 12px; border-bottom: 2.5px solid #1a1d23; margin-bottom: 16px;
}}
.header-left   {{ display: flex; flex-direction: column; gap: 3px; }}
.brand-name    {{ font-size: 20px; font-weight: 700; letter-spacing: 0.5px; color: #1a1d23; }}
.tx-badge {{
  display: inline-block; background: #1a1d23; color: #fff;
  font-family: {mono_font}; font-size: 9px; font-weight: 700; letter-spacing: 2px;
  padding: 3px 10px; border-radius: 3px; margin-top: 2px;
}}
.header-right  {{ text-align: right; display: flex; flex-direction: column; gap: 4px; align-items: flex-end; }}
.header-label  {{ font-size: 9px; font-weight: 500; letter-spacing: 2px; color: #888; }}
.customer-name {{ font-size: 16px; font-weight: 700; color: #1a1d23; }}

/* ── Summary strip (table-based for WeasyPrint compat) ── */
.summary-table {{
  width: 100%; border-collapse: collapse;
  border: 1.5px solid #1a1d23; border-radius: 4px;
  margin-bottom: 18px;
}}
.summary-table td {{
  padding: 9px 10px; border-right: 1px solid #d0d0d0; vertical-align: top;
  width: 25%;
}}
.summary-table td:last-child {{ border-right: none; }}
.summary-table td.highlight  {{ background: #1a1d23; }}
.s-label {{
  font-size: 8px; font-weight: 600; letter-spacing: 1.5px;
  text-transform: uppercase; color: #888; margin-bottom: 4px;
}}
.highlight .s-label {{ color: rgba(255,255,255,0.5); }}
.s-value {{ font-family: {mono_font}; font-size: 13px; font-weight: 600; color: #1a1d23; }}
.highlight .s-value {{ color: #fff; }}

/* ── Table section header ── */
.table-header {{
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 5px; padding-bottom: 4px; border-bottom: 1.5px solid #1a1d23;
}}
.table-title {{ font-size: 11px; font-weight: 700; }}
.tx-count    {{ font-family: {mono_font}; font-size: 9px; color: #888; font-weight: 500; }}

/* ── Transaction table ── */
table.tx-table {{
  width: 100%; border-collapse: collapse; font-size: 10px; table-layout: fixed;
}}
thead tr  {{ background: #f0f0f0; }}
thead th  {{
  padding: 5px 6px; text-align: left;
  font-size: 8.5px; font-weight: 700; color: #333;
  border-bottom: 1.5px solid #888; border-top: 1px solid #bbb;
  letter-spacing: 0.3px; white-space: nowrap;
}}
thead th.th-right  {{ text-align: right; }}
thead th.th-center {{ text-align: center; }}

tbody tr {{ border-bottom: 1px solid #ebebeb; }}
tbody tr:nth-child(even) {{ background: #f8f8f8; }}
tbody tr:last-child {{ border-bottom: none; }}
td {{ padding: 4px 6px; vertical-align: middle; }}

.td-num     {{ font-family: {mono_font}; font-size: 8.5px; color: #aaa; text-align: center; }}
.td-date    {{ font-family: {mono_font}; font-size: 9.5px; color: #444; white-space: nowrap; }}
.td-amount  {{ font-family: {mono_font}; font-size: 10.5px; font-weight: 600; color: #1a1d23; text-align: right; }}
.td-method  {{ font-size: 9px; color: #555; }}
.td-balance {{ font-family: {mono_font}; font-size: 9.5px; color: #666; text-align: right; }}

.badge-cash   {{
  display: inline-block; background: #eef4f0; color: #2d6a44;
  font-size: 8px; font-weight: 600; padding: 1px 6px;
  border-radius: 2px; letter-spacing: 0.3px;
}}
.badge-online {{
  display: inline-block; background: #eef0ff; color: #3142a8;
  font-size: 8px; font-weight: 600; padding: 1px 6px;
  border-radius: 2px; letter-spacing: 0.3px;
}}

/* ── Year band ── */
tr.year-band td {{
  background: #f0f0f0 !important;
  padding: 3px 6px; font-size: 8px; font-weight: 700;
  letter-spacing: 2px; color: #888; text-transform: uppercase;
  border-bottom: none !important;
}}

/* ── Total row ── */
tr.total-row td {{
  background: #1a1d23 !important;
  padding: 7px 6px; color: #fff; font-weight: 700;
  font-size: 10.5px; border-bottom: none !important;
}}
.total-label   {{ font-family: {body_font}; }}
.total-value   {{ font-family: {mono_font}; text-align: right; }}
.total-balance {{ font-family: {mono_font}; text-align: right; color: #ff8a80; }}

/* ── Footer ── */
.report-footer {{
  margin-top: 20px; padding-top: 10px; border-top: 1px solid #ccc;
  display: flex; justify-content: space-between; align-items: center;
}}
.footer-left  {{ font-size: 9px; color: #777; }}
.footer-right {{ font-size: 9px; color: #777; font-family: {mono_font}; }}
"""


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/{product}/{customer_id}/export.pdf")
def export_customer_pdf(
    product: str,
    customer_id: int,
    lang: str = "en",
    filter: str = "all",
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    # ── Fetch customer + transactions ─────────────────────────────────────────
    if product == "edi":
        customer = session.get(EdiCustomer, customer_id)
        txns     = EdiTransactionService(session).list_by_customer(customer_id)
        name_map = session.get(EdiNameMap, customer_id)
    elif product == "iop":
        customer = session.get(IopCustomer, customer_id)
        txns     = IopTransactionService(session).list_by_customer(customer_id)
        name_map = session.get(IopNameMap, customer_id)
    else:
        raise HTTPException(400, "product must be 'edi' or 'iop'")

    if not customer:
        raise HTTPException(404, "Customer not found")

    # ── Names ─────────────────────────────────────────────────────────────────
    tamil_name   = (name_map.customer_name_ta or "").strip() if name_map else ""
    english_name = (customer.customer_name or "Customer").strip()
    display_name = tamil_name or english_name
    loan_start   = customer.loan_start_date
    loan_amount  = float(customer.loan_amount or 0)
    loan_year    = str(loan_start.year) if loan_start else "—"

    # ── Only PAID transactions sorted by date ────────────────────────────────
    paid_txns = sorted(
        [t for t in txns if t.payment_status == "PAID"],
        key=lambda t: str(t.collection_date or ""),
    )
    n           = len(paid_txns)
    total_paid  = sum(float(t.amount) for t in paid_txns)
    outstanding = loan_amount - total_paid

    # ── Summary strip ─────────────────────────────────────────────────────────
    summary_cells = [
        ("கடன் தொடங்கிய",   _fmt_date(loan_start), False),
        ("கடன் தொகை",       _fmt_amt(loan_amount),  False),
        ("மொத்த செலுத்தல்", _fmt_amt(total_paid),   False),
        ("நிலுவை தொகை",     _fmt_amt(outstanding),  True),   # dark highlight
    ]
    summary_cells_html = "".join(
        f'<td class="{"highlight" if hi else ""}">'
        f'<div class="s-label">{lbl}</div>'
        f'<div class="s-value">{val}</div>'
        f'</td>'
        for lbl, val, hi in summary_cells
    )

    # ── Transaction rows with year-band separators and running balance ────────
    rows_html       = ""
    running_balance = loan_amount
    current_year    = None

    for idx, t in enumerate(paid_txns, 1):
        amt             = float(t.amount)
        running_balance -= amt
        txn_year = str(t.collection_date.year) if t.collection_date else ""

        if txn_year and txn_year != current_year:
            rows_html   += f'<tr class="year-band"><td colspan="5">{txn_year}</td></tr>\n'
            current_year = txn_year

        badge = _method_badge(t.payment_mode or "")
        rows_html += (
            f'<tr>'
            f'<td class="td-num">{idx}</td>'
            f'<td class="td-date">{_fmt_date(t.collection_date)}</td>'
            f'<td class="td-amount">{_fmt_amt(amt)}</td>'
            f'<td class="td-method">{badge}</td>'
            f'<td class="td-balance">{_fmt_amt(running_balance)}</td>'
            f'</tr>\n'
        )

    # ── Footer ────────────────────────────────────────────────────────────────
    start_str    = _fmt_date(loan_start)
    footer_left  = f"GG Finance · {display_name} · {start_str} முதல்"
    footer_right = f"கடன் #TX-{loan_year}"

    # ── Assemble HTML ─────────────────────────────────────────────────────────
    font_face, body_font, mono_font = _font_face_css()
    css  = _tx_css(font_face, body_font, mono_font)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>{css}</style>
</head><body>

<div class="header-top">
  <div class="header-left">
    <div class="brand-name">GG Finance</div>
    <div class="tx-badge">TRANSACTIONS</div>
  </div>
  <div class="header-right">
    <div class="header-label">கடன் பரிவர்த்தனை அறிக்கை</div>
    <div class="customer-name">{display_name}</div>
  </div>
</div>

<table class="summary-table">
  <tr>{summary_cells_html}</tr>
</table>

<div class="table-header">
  <div class="table-title">பரிவர்த்தனை விவரங்கள்</div>
  <div class="tx-count">{n} பரிவர்த்தனைகள்</div>
</div>

<table class="tx-table">
  <colgroup>
    <col style="width:22px"/>
    <col style="width:82px"/>
    <col style="width:72px"/>
    <col style="width:90px"/>
    <col style="width:72px"/>
  </colgroup>
  <thead>
    <tr>
      <th class="th-center">#</th>
      <th>தேதி</th>
      <th class="th-right">தொகை</th>
      <th>முறை</th>
      <th class="th-right">நிலுவை</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
    <tr class="total-row">
      <td colspan="2" class="total-label">மொத்தம் · {n} பரிவர்த்தனைகள்</td>
      <td class="total-value">{_fmt_amt(total_paid)}</td>
      <td></td>
      <td class="total-balance">{_fmt_amt(outstanding)}</td>
    </tr>
  </tbody>
</table>

<div class="report-footer">
  <div class="footer-left">{footer_left}</div>
  <div class="footer-right">{footer_right}</div>
</div>

</body></html>"""

    try:
        pdf_bytes = _render_pdf(html)
    except Exception:
        log.error("TX PDF render failed:\n%s", traceback.format_exc())
        raise HTTPException(500, "PDF generation failed")

    safe_name = english_name.replace(" ", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_transactions.pdf"'},
    )
