"""Daily Print PDF — EDI and IOP.

Renderer: WeasyPrint (Cairo/Pango) for full CSS support:
  - display:flex  (header, section header, footer)
  - nth-child     (alternating row colours)
  - border-radius (section number badge, IOP pills)
  - Tamil shaping via HarfBuzz through Pango

Design matches reference ZIPs exactly:
  EDI  — brand+badge left | label+date right
  IOP  — centered brand, subtitle, date+count pills

Column selection: ?cols=id,name,date,loan,balance,days,collect
Two-column layout: ?two_col=true
"""
import io
import logging
import os
import pathlib
import traceback
from collections import OrderedDict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session

router = APIRouter()
log = logging.getLogger(__name__)

_FONTS_DIR  = pathlib.Path(__file__).parent.parent.parent / "fonts"
_TAMIL_FONT = "NotoSansTamil-Regular.ttf"
_MONO_FONT  = "IBMPlexMono-Regular.ttf"

# ── Column registry: key → (header label, align, fixed_width, dashed_left) ──────
_COLS: dict[str, tuple] = {
    "id":      ("எண்",             "left",  "40px",  False),
    "name":    ("பெயர்",            "left",  None,    False),
    "date":    ("தொடங்கிய",         "left",  "88px",  False),
    "loan":    ("கடன் ₹",           "right", "80px",  False),
    "balance": ("நிலுவை ₹",         "right", "80px",  False),
    "days":    ("நாட்கள்",          "right", "50px",  False),
    "collect": ("இன்று வசூல் ₹",    "right", "80px",  True),
}
_DEFAULT_COLS = "id,name,date,loan,balance,days,collect"


# ── Utilities ────────────────────────────────────────────────────────────────────

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


def _fmt_date(d) -> str:
    if not d:
        return "—"
    parts = str(d).split("-")
    return f"{parts[2]}-{parts[1]}-{parts[0]}" if len(parts) == 3 else str(d)


def _parse_cols(cols_str: str) -> list[tuple]:
    keys = [k.strip() for k in cols_str.split(",") if k.strip() in _COLS]
    if not keys:
        keys = list(_COLS.keys())
    return [(k, *_COLS[k]) for k in keys]


def _font_face_css() -> tuple[str, str, str]:
    """(font_face_css, body_font_family, mono_font_family)"""
    t = _FONTS_DIR / _TAMIL_FONT
    m = _FONTS_DIR / _MONO_FONT
    face = ""
    if t.exists():
        face += f'@font-face {{font-family:"NotoTamil";src:url("{t.as_uri()}");font-weight:400 700;}}\n'
    if m.exists():
        face += f'@font-face {{font-family:"IBMPlexMono";src:url("{m.as_uri()}");font-weight:400 500;}}\n'
    body = '"NotoTamil", sans-serif' if t.exists() else "sans-serif"
    mono = '"IBMPlexMono", monospace' if m.exists() else "monospace"
    return face, body, mono


def _render_pdf(html: str, landscape: bool = False) -> bytes:
    from weasyprint import HTML, CSS  # noqa
    size = "A4 landscape" if landscape else "A4"
    page_css = CSS(string=f"@page {{ size: {size}; margin: 0.75in; }}")
    buf = io.BytesIO()
    HTML(string=html, base_url=None).write_pdf(buf, stylesheets=[page_css])
    return buf.getvalue()


# ── Shared CSS ───────────────────────────────────────────────────────────────────

def _base_css(font_face: str, body_font: str, mono_font: str) -> str:
    return f"""
{font_face}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: {body_font}; color: #1a1d23; font-size: 12px; }}

/* ── Section ── */
.section-group {{ margin-bottom: 22px; break-inside: avoid; page-break-inside: avoid; }}
.section-header {{
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 6px; padding-bottom: 5px;
  border-bottom: 1.5px solid #1a1d23;
}}
.section-number {{
  width: 20px; height: 20px; flex-shrink: 0;
  border: 1.5px solid #1a1d23; border-radius: 4px;
  font-size: 10px; font-weight: 700; color: #1a1d23;
  display: flex; align-items: center; justify-content: center;
}}
.section-title {{ font-size: 13px; font-weight: 700; color: #1a1d23; }}

/* ── Table ── */
table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
thead tr {{ background: #f0f0f0; }}
thead th {{
  padding: 6px 9px; text-align: left;
  font-size: 10px; font-weight: 700; color: #222;
  letter-spacing: 0.7px;
  border-bottom: 1.5px solid #888; border-top: 1px solid #bbb;
}}
thead th.num {{ text-align: right; }}
thead th.col-collect {{ border-left: 1px dashed #bbb; }}
tbody tr {{ border-bottom: 1px solid #e0e0e0; }}
tbody tr:nth-child(even) {{ background: #f8f8f8; }}
tbody tr:last-child {{ border-bottom: none; }}
td {{ padding: 6px 9px; vertical-align: middle; }}

/* ── Cell types ── */
.td-id   {{ font-family: {mono_font}; font-size: 10.5px; color: #555; font-weight: 500; width: 40px; }}
.td-name {{ font-weight: 500; color: #1a1d23; }}
.td-date {{ font-family: {mono_font}; font-size: 10.5px; color: #555; white-space: nowrap; }}
.td-amount {{ text-align: right; font-family: {mono_font}; font-size: 11.5px; font-weight: 500; }}
.td-loan    {{ color: #222; }}
.td-balance {{ font-weight: 700; }}
.td-balance.positive {{ color: #1a1d23; }}
.td-balance.zero     {{ color: #555; }}
.td-balance.negative {{ color: #1a1d23; text-decoration: underline; }}
.td-days        {{ text-align: right; font-family: {mono_font}; font-size: 10.5px; color: #555; }}
.td-days.overdue {{ color: #1a1d23; font-weight: 700; }}
.td-days.dash   {{ color: #999; }}
.td-collect {{
  text-align: right; font-family: {mono_font}; font-size: 11.5px;
  color: #1a1d23; border-left: 1px dashed #bbb; min-width: 80px;
}}

/* ── Grand total ── */
.total-row {{ background: #1a1d23 !important; border-top: 2px solid #1a1d23; }}
.total-row td {{ padding: 9px; color: #fff; font-weight: 700; font-size: 12.5px; }}
.total-row .td-id {{ color: rgba(255,255,255,0.45); font-size: 10.5px; }}
.total-row .td-days  {{ color: rgba(255,255,255,0.4); }}
.total-row .td-collect {{ color: rgba(255,255,255,0.4); }}

/* ── Footer ── */
.report-footer {{
  margin-top: 28px; padding-top: 12px; border-top: 1px solid #ccc;
  display: flex; justify-content: space-between; align-items: center;
}}
.footer-left  {{ font-size: 10px; color: #777; }}
.footer-right {{ font-size: 10px; color: #777; font-family: {mono_font}; }}

/* ── Two-column layout ── */
.two-col-row {{
  display: flex; gap: 24px; margin-bottom: 0;
}}
.two-col-row > div {{ flex: 1; min-width: 0; }}
"""


# ── HTML builders ────────────────────────────────────────────────────────────────

def _section_block(
    sec_num: int,
    grp_label: str,
    col_defs: list[tuple],
    rows: list,
    today: date,
    last_paid_map: dict,
) -> str:
    # Column headers
    headers = ""
    for key, label, align, width, dashed in col_defs:
        cls = "num" if align == "right" else ""
        if dashed:
            cls = (cls + " col-collect").strip()
        style = f'width:{width};' if width else ""
        headers += f'<th class="{cls}" style="{style}">{label}</th>'

    rows_html = ""
    for r in rows:
        loan    = float(r.loan_amount or 0)
        balance = float(r.outstanding_balance or 0)

        last_paid = last_paid_map.get(r.customer_id)
        if last_paid:
            days_ago  = (today - last_paid).days
            days_str  = str(days_ago)
            days_cls  = "td-days overdue" if days_ago > 7 else "td-days"
        else:
            days_str = "—"
            days_cls = "td-days dash"

        bal_cls = "positive" if balance > 0 else ("zero" if balance == 0 else "negative")

        cells = ""
        for key, _, align, width, dashed in col_defs:
            if key == "id":
                cells += f'<td class="td-id">{r.customer_id}</td>'
            elif key == "name":
                cells += f'<td class="td-name">{r.ta_name or "—"}</td>'
            elif key == "date":
                cells += f'<td class="td-date">{_fmt_date(r.loan_start_date)}</td>'
            elif key == "loan":
                cells += f'<td class="td-amount td-loan">{_fmt_amt(loan)}</td>'
            elif key == "balance":
                cells += f'<td class="td-amount td-balance {bal_cls}">{_fmt_amt(balance)}</td>'
            elif key == "days":
                cells += f'<td class="{days_cls}">{days_str}</td>'
            elif key == "collect":
                cells += '<td class="td-collect"></td>'

        rows_html += f'<tr>{cells}</tr>\n'

    return f"""<div class="section-group">
  <div class="section-header">
    <div class="section-number">{sec_num}</div>
    <div class="section-title">{grp_label}</div>
  </div>
  <table>
    <thead><tr>{headers}</tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""


def _grand_total_html(
    total_loan: float,
    total_balance: float,
    col_defs: list[tuple],
) -> str:
    cells = ""
    label_done = False
    for key, _, align, width, dashed in col_defs:
        if key == "id":
            cells += '<td class="td-id">—</td>'
        elif key == "name":
            label = "மொத்தம்" if not label_done else ""
            label_done = True
            cells += f'<td style="font-size:13px;">{label}</td>'
        elif key == "date":
            cells += '<td></td>'
        elif key == "loan":
            cells += f'<td class="td-amount">{_fmt_amt(total_loan)}</td>'
        elif key == "balance":
            cells += f'<td class="td-amount">{_fmt_amt(total_balance)}</td>'
        elif key == "days":
            cells += '<td class="td-days">—</td>'
        elif key == "collect":
            cells += '<td class="td-collect">—</td>'
    return f'<table style="margin-top:8px;"><tbody><tr class="total-row">{cells}</tr></tbody></table>'


def _footer_html(left_text: str, right_text: str) -> str:
    return f"""<div class="report-footer">
  <div class="footer-left">{left_text}</div>
  <div class="footer-right">{right_text}</div>
</div>"""


def _layout_sections(blocks: list[str], two_col: bool) -> str:
    if not two_col or len(blocks) <= 1:
        return "\n".join(blocks)
    rows = []
    for i in range(0, len(blocks), 2):
        left  = blocks[i]
        right = blocks[i + 1] if i + 1 < len(blocks) else ""
        rows.append(
            f'<div class="two-col-row">'
            f'<div>{left}</div>'
            f'<div>{right}</div>'
            f'</div>'
        )
    return "\n".join(rows)


# ── EDI endpoint ─────────────────────────────────────────────────────────────────

@router.get("/edi")
def edi_daily_print(
    cols: str = Query(default=_DEFAULT_COLS),
    two_col: bool = Query(default=False),
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    rows = session.exec(text("""
        SELECT
            c.customer_id,
            c.customer_segment_id,
            c.loan_amount,
            c.outstanding_balance,
            c.loan_start_date,
            COALESCE(nm.customer_name_ta, c.customer_name, '') AS ta_name,
            COALESCE(gm.customer_segment_name_ta, gm.customer_segment_name_en, '') AS grp_ta,
            COALESCE(gm.customer_segment_name_en, '') AS grp_en
        FROM tbl_edi_customer c
        LEFT JOIN tbl_edi_name_map nm  ON nm.customer_id = c.customer_id
        LEFT JOIN tbl_edi_group_map gm ON gm.customer_segment_id = c.customer_segment_id
        WHERE c.is_closed = false AND COALESCE(c.ignore, false) = false
        ORDER BY c.customer_segment_id ASC NULLS LAST, c.loan_start_date ASC, c.customer_id ASC
    """)).fetchall()

    customer_ids = [r.customer_id for r in rows]
    last_paid_map: dict = {}
    if customer_ids:
        id_list = ",".join(str(i) for i in customer_ids)
        paid_rows = session.exec(text(f"""
            SELECT customer_id, MAX(collection_date) AS last_paid
            FROM tbl_edi_transactions
            WHERE customer_id IN ({id_list}) AND payment_status = 'PAID'
            GROUP BY customer_id
        """)).fetchall()
        last_paid_map = {r.customer_id: r.last_paid for r in paid_rows}

    today     = date.today()
    today_str = today.strftime("%d-%m-%Y")
    col_defs  = _parse_cols(cols)
    font_face, body_font, mono_font = _font_face_css()
    css = _base_css(font_face, body_font, mono_font)

    # Add EDI-specific header CSS
    css += f"""
/* ── EDI Header ── */
.header-top {{
  display: flex; align-items: flex-end; justify-content: space-between;
  padding-bottom: 12px; border-bottom: 2.5px solid #1a1d23; margin-bottom: 22px;
}}
.header-left {{ display: flex; flex-direction: column; gap: 3px; }}
.brand-name  {{ font-size: 22px; font-weight: 700; letter-spacing: 0.5px; color: #1a1d23; }}
.edi-badge   {{
  display: inline-block; background: #1a1d23; color: #fff;
  font-size: 10px; font-weight: 700; letter-spacing: 2px;
  padding: 3px 10px; border-radius: 3px; margin-top: 2px; width: fit-content;
}}
.header-right {{ text-align: right; display: flex; flex-direction: column; gap: 5px; align-items: flex-end; }}
.header-date  {{ font-family: {mono_font}; font-size: 14px; font-weight: 700; color: #1a1d23; }}
.header-label {{ font-size: 9px; font-weight: 500; letter-spacing: 2px; color: #888; }}
"""

    # Build sections
    groups: OrderedDict = OrderedDict()
    total_loan = total_balance = 0.0
    for r in rows:
        key = str(r.customer_segment_id or "none")
        if key not in groups:
            groups[key] = {"label": r.grp_ta or r.grp_en or f"Group {r.customer_segment_id}", "rows": []}
        groups[key]["rows"].append(r)
        total_loan    += float(r.loan_amount or 0)
        total_balance += float(r.outstanding_balance or 0)

    blocks = [
        _section_block(i, g["label"], col_defs, g["rows"], today, last_paid_map)
        for i, (_, g) in enumerate(groups.items(), 1)
    ]

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>{css}</style>
</head><body>

<div class="header-top">
  <div class="header-left">
    <div class="brand-name">GG Finance</div>
    <div class="edi-badge">EDI</div>
  </div>
  <div class="header-right">
    <div class="header-label">தினசரி கணக்கு அறிக்கை</div>
    <div class="header-date">{today_str}</div>
  </div>
</div>

{_layout_sections(blocks, two_col)}

{_grand_total_html(total_loan, total_balance, col_defs)}

{_footer_html(
    f"GG Finance · EDI தினசரி அறிக்கை · {today_str}",
    f"{len(rows)} வாடிக்கையாளர்கள்",
)}

</body></html>"""

    try:
        pdf_bytes = _render_pdf(html, landscape=False)
    except Exception:
        log.error("EDI PDF render failed:\n%s", traceback.format_exc())
        raise HTTPException(500, "PDF generation failed")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="EDI_Daily_{today_str}.pdf"'},
    )


# ── IOP endpoint ─────────────────────────────────────────────────────────────────

@router.get("/iop")
def iop_daily_print(
    cols: str = Query(default=_DEFAULT_COLS),
    two_col: bool = Query(default=False),
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    rows = session.exec(text("""
        SELECT
            c.customer_id,
            c.customer_segment_id,
            c.loan_amount,
            c.outstanding_balance,
            c.loan_start_date,
            COALESCE(nm.customer_name_ta, c.customer_name, '') AS ta_name,
            COALESCE(gm.customer_segment_name_ta, gm.customer_segment_name_en, '') AS grp_ta,
            COALESCE(gm.customer_segment_name_en, '') AS grp_en
        FROM tbl_iop_customer c
        LEFT JOIN tbl_iop_name_map nm  ON nm.customer_id = c.customer_id
        LEFT JOIN tbl_iop_group_map gm ON gm.customer_segment_id = c.customer_segment_id
        WHERE c.is_closed = false AND COALESCE(c.ignore, false) = false
        ORDER BY c.customer_segment_id ASC NULLS LAST, c.loan_start_date ASC, c.customer_id ASC
    """)).fetchall()

    customer_ids = [r.customer_id for r in rows]
    last_paid_map: dict = {}
    if customer_ids:
        id_list = ",".join(str(i) for i in customer_ids)
        paid_rows = session.exec(text(f"""
            SELECT customer_id, MAX(collection_date) AS last_paid
            FROM tbl_iop_transactions
            WHERE customer_id IN ({id_list}) AND payment_status = 'PAID'
            GROUP BY customer_id
        """)).fetchall()
        last_paid_map = {r.customer_id: r.last_paid for r in paid_rows}

    today     = date.today()
    today_str = today.strftime("%d-%m-%Y")
    col_defs  = _parse_cols(cols)
    font_face, body_font, mono_font = _font_face_css()
    css = _base_css(font_face, body_font, mono_font)

    # Add IOP-specific header CSS
    css += f"""
/* ── IOP Header ── */
.report-header   {{ border-bottom: 2.5px solid #1a1d23; padding-bottom: 14px; margin-bottom: 22px; }}
.header-center   {{ text-align: center; }}
.brand-name      {{ font-size: 24px; font-weight: 700; letter-spacing: 1px; color: #1a1d23; margin-bottom: 2px; }}
.brand-sub       {{ font-size: 10px; font-weight: 500; letter-spacing: 2.5px; color: #555; margin-bottom: 6px; }}
.header-meta-line {{
  display: flex; justify-content: center; gap: 18px;
  font-size: 11px; color: #444; margin-top: 6px;
}}
.header-meta-line span {{ border: 1px solid #ccc; border-radius: 3px; padding: 2px 10px; }}
.header-divider  {{ height: 1px; background: #ccc; margin-top: 14px; }}
"""

    groups: OrderedDict = OrderedDict()
    total_loan = total_balance = 0.0
    for r in rows:
        key = str(r.customer_segment_id or "none")
        if key not in groups:
            groups[key] = {"label": r.grp_ta or r.grp_en or f"Group {r.customer_segment_id}", "rows": []}
        groups[key]["rows"].append(r)
        total_loan    += float(r.loan_amount or 0)
        total_balance += float(r.outstanding_balance or 0)

    blocks = [
        _section_block(i, g["label"], col_defs, g["rows"], today, last_paid_map)
        for i, (_, g) in enumerate(groups.items(), 1)
    ]

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>{css}</style>
</head><body>

<div class="report-header">
  <div class="header-center">
    <div class="brand-name">GG Finance</div>
    <div class="brand-sub">IOP · வட்டி வசூல் பட்டியல்</div>
    <div class="header-meta-line">
      <span>{today_str}</span>
      <span>{len(rows)} வாடிக்கையாளர்கள்</span>
    </div>
  </div>
  <div class="header-divider"></div>
</div>

{_layout_sections(blocks, two_col)}

{_grand_total_html(total_loan, total_balance, col_defs)}

{_footer_html(
    f"GG Finance · IOP · வட்டி வசூல் பட்டியல் · {today_str}",
    f"{len(rows)} வாடிக்கையாளர்கள்",
)}

</body></html>"""

    try:
        pdf_bytes = _render_pdf(html, landscape=False)
    except Exception:
        log.error("IOP PDF render failed:\n%s", traceback.format_exc())
        raise HTTPException(500, "PDF generation failed")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="IOP_Interest_{today_str}.pdf"'},
    )
