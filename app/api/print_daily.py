"""Daily Print PDF — EDI and IOP.

Design: exact match to GG Finance Report.dc.html from design ZIPs.
  - Near-black #1a1d23 palette, print-safe
  - IBM Plex Mono for IDs / dates / amounts; Noto Sans Tamil for Tamil
  - Numbered group sections, one table per group
  - Grand total row in #1a1d23 with white text
  - Configurable columns via ?cols= (comma-separated)
  - Two-column group layout via ?two_col=true

EDI header: brand+badge left | date right
IOP header: centered brand, subtitle, date+count pills
"""
import io
import logging
import os
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

_FONTS_DIR  = os.path.join(os.path.dirname(__file__), "..", "..", "fonts")
_TAMIL_FONT = "NotoSansTamil-Regular.ttf"
_MONO_FONT  = "IBMPlexMono-Regular.ttf"

# ── Column registry: key → (Tamil label, align, fixed_width, dashed_left) ─────
_COLS: dict[str, tuple] = {
    "id":      ("ID",         "left",  "40px", False),
    "name":    ("பெயர்",      "left",  None,   False),
    "date":    ("தொடக்கம்",   "left",  "88px", False),
    "loan":    ("கடன் ₹",     "right", "80px", False),
    "balance": ("நிலுவை ₹",   "right", "80px", False),
    "days":    ("நாட்கள்",    "right", "50px", False),
    "collect": ("வசூல் ₹",    "right", "80px", True),
}
_DEFAULT_COLS = "id,name,date,loan,balance,days,collect"


# ── Utility ─────────────────────────────────────────────────────────────────────

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
    """Parse ?cols= into list of (key, label, align, width, dashed)."""
    keys = [k.strip() for k in cols_str.split(",") if k.strip() in _COLS]
    if not keys:
        keys = list(_COLS.keys())
    return [(k, *_COLS[k]) for k in keys]


def _render_pdf(html: str, landscape: bool = False) -> bytes:
    import fitz
    has = os.path.exists(os.path.join(_FONTS_DIR, _TAMIL_FONT)) or \
          os.path.exists(os.path.join(_FONTS_DIR, _MONO_FONT))
    archive = fitz.Archive(_FONTS_DIR) if has else None
    story   = fitz.Story(html, archive=archive) if archive else fitz.Story(html)
    buf     = io.BytesIO()
    writer  = fitz.DocumentWriter(buf)
    A4      = fitz.paper_rect("a4")
    rect    = fitz.Rect(0, 0, A4.y1, A4.x1) if landscape else A4
    margin  = 36
    clip    = fitz.Rect(rect.x0 + margin, rect.y0 + margin,
                        rect.x1 - margin, rect.y1 - margin)
    more = True
    while more:
        dev = writer.begin_page(rect)
        more, _ = story.place(clip)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return buf.getvalue()


def _font_css() -> tuple[str, str, str]:
    """(font_face_css, body_font, mono_font)"""
    face = ""
    t = os.path.join(_FONTS_DIR, _TAMIL_FONT)
    m = os.path.join(_FONTS_DIR, _MONO_FONT)
    if os.path.exists(t):
        face += f'@font-face {{ font-family:"NotoTamil"; src:url("{_TAMIL_FONT}"); }}\n'
    if os.path.exists(m):
        face += f'@font-face {{ font-family:"IBMPlexMono"; src:url("{_MONO_FONT}"); }}\n'
    body = '"NotoTamil", sans-serif' if os.path.exists(t) else "sans-serif"
    mono = '"IBMPlexMono", monospace' if os.path.exists(m) else "monospace"
    return face, body, mono


# ── Shared HTML builders ────────────────────────────────────────────────────────

_TH = (
    "padding:3px 7px;text-align:left;font-size:8px;font-weight:700;color:#222;"
    "letter-spacing:0.5px;border-bottom:1.5px solid #888;border-top:1px solid #bbb;"
    "background:#f0f0f0;"
)


def _section_block(
    sec_num: int,
    grp_label: str,
    col_defs: list[tuple],
    mono_font: str,
    rows: list,
    today: date,
    last_paid_map: dict,   # customer_id → date | None
) -> str:
    """One numbered section: header + table (no grand total)."""
    headers = ""
    for key, label, align, width, dashed in col_defs:
        th = _TH
        if align == "right":
            th += "text-align:right;"
        if width:
            th += f"width:{width};"
        if dashed:
            th += "border-left:1px dashed #bbb;"
        headers += f'<th style="{th}">{label}</th>'

    rows_html = ""
    for i, r in enumerate(rows):
        row_bg     = "#f8f8f8" if i % 2 == 1 else "#ffffff"
        row_border = "" if i == len(rows) - 1 else "border-bottom:1px solid #e8e8e8;"

        loan    = float(r.loan_amount or 0)
        balance = float(r.outstanding_balance or 0)

        last_paid = last_paid_map.get(r.customer_id)
        if last_paid:
            days_ago   = (today - last_paid).days
            days_str   = str(days_ago)
            days_style = "font-weight:700;color:#1a1d23;" if days_ago > 7 else ""
        else:
            days_str   = "—"
            days_style = "color:#999;"

        cells = ""
        for key, _, align, width, dashed in col_defs:
            base = f"padding:3px 7px;{row_border}"
            if align == "right":
                base += "text-align:right;"
            if width:
                base += f"width:{width};"
            if dashed:
                base += "border-left:1px dashed #bbb;"

            if key == "id":
                cells += (
                    f'<td style="{base}font-family:{mono_font};'
                    f'font-size:8.5px;color:#666;font-weight:500;">'
                    f'{r.customer_id}</td>'
                )
            elif key == "name":
                cells += (
                    f'<td style="{base}font-size:9px;font-weight:500;color:#1a1d23;">'
                    f'{r.ta_name or "—"}</td>'
                )
            elif key == "date":
                cells += (
                    f'<td style="{base}font-family:{mono_font};'
                    f'font-size:8.5px;color:#666;">'
                    f'{_fmt_date(r.loan_start_date)}</td>'
                )
            elif key == "loan":
                cells += (
                    f'<td style="{base}font-family:{mono_font};'
                    f'font-size:9px;font-weight:500;color:#333;">'
                    f'{_fmt_amt(loan)}</td>'
                )
            elif key == "balance":
                bal_col = "color:#888;" if balance == 0 else "color:#1a1d23;"
                cells += (
                    f'<td style="{base}font-family:{mono_font};'
                    f'font-size:9px;font-weight:700;{bal_col}">'
                    f'{_fmt_amt(balance)}</td>'
                )
            elif key == "days":
                cells += (
                    f'<td style="{base}font-family:{mono_font};'
                    f'font-size:8.5px;{days_style}">'
                    f'{days_str}</td>'
                )
            elif key == "collect":
                cells += (
                    f'<td style="{base}font-family:{mono_font};'
                    f'font-size:9px;color:#1a1d23;min-width:70px;">'
                    f'</td>'
                )

        rows_html += f'<tr style="background:{row_bg};">{cells}</tr>\n'

    return f"""<div style="margin-bottom:14px;">
  <table style="border:none;width:100%;border-bottom:1.5px solid #1a1d23;margin-bottom:4px;border-collapse:separate;">
    <tr>
      <td style="border:none;width:24px;padding:2px 6px 4px 0;vertical-align:middle;">
        <table style="border:1.5px solid #1a1d23;border-radius:3px;width:18px;border-collapse:separate;">
          <tr><td style="border:none;text-align:center;font-size:8px;font-weight:700;color:#1a1d23;padding:1px 3px;">{sec_num}</td></tr>
        </table>
      </td>
      <td style="border:none;font-size:10.5px;font-weight:700;color:#1a1d23;padding-bottom:4px;">{grp_label}</td>
    </tr>
  </table>
  <table>
    <tr style="background:#f0f0f0;">{headers}</tr>
    {rows_html}
  </table>
</div>"""


def _grand_total_html(
    total_loan: float,
    total_balance: float,
    col_defs: list[tuple],
    mono_font: str,
) -> str:
    cells = ""
    name_done = False
    for key, _, align, width, dashed in col_defs:
        base = "padding:5px 7px;color:#fff;font-weight:700;font-size:9px;"
        if align == "right":
            base += "text-align:right;"
        if dashed:
            base += "border-left:1px dashed #555;"

        if key == "id":
            cells += (
                f'<td style="{base}font-family:{mono_font};'
                f'font-size:8.5px;color:#8a9099;width:40px;">—</td>'
            )
        elif key == "name":
            label = "மொத்தம்" if not name_done else ""
            name_done = True
            cells += f'<td style="{base}font-size:9px;">{label}</td>'
        elif key == "date":
            cells += f'<td style="{base}"></td>'
        elif key == "loan":
            cells += (
                f'<td style="{base}font-family:{mono_font};font-size:9px;">'
                f'{_fmt_amt(total_loan)}</td>'
            )
        elif key == "balance":
            cells += (
                f'<td style="{base}font-family:{mono_font};font-size:9px;">'
                f'{_fmt_amt(total_balance)}</td>'
            )
        elif key == "days":
            cells += f'<td style="{base}font-size:8.5px;color:#8a9099;">—</td>'
        elif key == "collect":
            cells += f'<td style="{base}min-width:70px;"></td>'

    return (
        f'<table style="margin-top:6px;border:none;">'
        f'<tr style="background:#1a1d23;">{cells}</tr>'
        f'</table>'
    )


def _footer_html(left_text: str, right_text: str, mono_font: str) -> str:
    return (
        f'<table style="border:none;border-top:1px solid #ccc;'
        f'margin-top:18px;width:100%;">'
        f'<tr>'
        f'<td style="border:none;padding-top:8px;font-size:8px;color:#999;">{left_text}</td>'
        f'<td style="border:none;padding-top:8px;text-align:right;'
        f'font-size:8px;color:#999;font-family:{mono_font};">{right_text}</td>'
        f'</tr></table>'
    )


def _layout_sections(blocks: list[str], two_col: bool) -> str:
    if not two_col or len(blocks) <= 1:
        return "\n".join(blocks)
    rows = []
    for i in range(0, len(blocks), 2):
        left  = blocks[i]
        right = blocks[i + 1] if i + 1 < len(blocks) else ""
        rows.append(
            f'<table style="border:none;width:100%;'
            f'border-collapse:separate;border-spacing:20px 0;">'
            f'<tr>'
            f'<td style="border:none;vertical-align:top;width:50%;">{left}</td>'
            f'<td style="border:none;vertical-align:top;width:50%;">{right}</td>'
            f'</tr></table>'
        )
    return "\n".join(rows)


# ── EDI daily print ─────────────────────────────────────────────────────────────

@router.get("/edi")
def edi_daily_print(
    cols: str = Query(default=_DEFAULT_COLS),
    two_col: bool = Query(default=False),
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    import fitz  # noqa

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

    # Fetch last paid dates separately (avoids correlated subquery per row)
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
    font_face, body_font, mono_font = _font_css()

    # Group by segment
    groups: OrderedDict = OrderedDict()
    total_loan    = 0.0
    total_balance = 0.0
    for r in rows:
        key = str(r.customer_segment_id or "none")
        if key not in groups:
            groups[key] = {
                "label": r.grp_ta or r.grp_en or f"Group {r.customer_segment_id}",
                "rows": [],
            }
        groups[key]["rows"].append(r)
        total_loan    += float(r.loan_amount or 0)
        total_balance += float(r.outstanding_balance or 0)

    # Build section blocks
    blocks = [
        _section_block(i, g["label"], col_defs, mono_font, g["rows"], today, last_paid_map)
        for i, (_, g) in enumerate(groups.items(), 1)
    ]

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
{font_face}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:{body_font}; color:#1a1d23; font-size:9px; line-height:1.4; }}
table {{ border-collapse:collapse; width:100%; }}
</style>
</head><body>

<!-- EDI Header: brand+badge left | date right -->
<table style="border:none;border-bottom:2px solid #1a1d23;padding-bottom:8px;margin-bottom:14px;width:100%;border-collapse:collapse;">
  <tr>
    <td style="border:none;vertical-align:bottom;padding-bottom:8px;">
      <div style="font-size:15px;font-weight:700;letter-spacing:0.3px;color:#1a1d23;margin-bottom:3px;">GG Finance</div>
      <table style="border:none;width:auto;border-collapse:separate;">
        <tr>
          <td style="border:none;background:#1a1d23;border-radius:2px;padding:2px 8px;font-size:8px;font-weight:700;letter-spacing:1.5px;color:#fff;">EDI</td>
        </tr>
      </table>
    </td>
    <td style="border:none;vertical-align:bottom;text-align:right;padding-bottom:8px;">
      <div style="font-size:7.5px;font-weight:500;letter-spacing:1.5px;color:#888;margin-bottom:3px;">தினசரி வசூல் பட்டியல்</div>
      <div style="font-family:{mono_font};font-size:11px;font-weight:700;color:#1a1d23;">{today_str}</div>
    </td>
  </tr>
</table>

{_layout_sections(blocks, two_col)}

{_grand_total_html(total_loan, total_balance, col_defs, mono_font)}

{_footer_html(
    f"GG Finance · EDI · தினசரி வசூல் பட்டியல் · {today_str}",
    f"{len(rows)} வாடிக்கையாளர்கள்",
    mono_font,
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


# ── IOP interest cadence print ──────────────────────────────────────────────────

@router.get("/iop")
def iop_daily_print(
    cols: str = Query(default=_DEFAULT_COLS),
    two_col: bool = Query(default=False),
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    import fitz  # noqa

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
    font_face, body_font, mono_font = _font_css()

    groups: OrderedDict = OrderedDict()
    total_loan    = 0.0
    total_balance = 0.0
    for r in rows:
        key = str(r.customer_segment_id or "none")
        if key not in groups:
            groups[key] = {
                "label": r.grp_ta or r.grp_en or f"Group {r.customer_segment_id}",
                "rows": [],
            }
        groups[key]["rows"].append(r)
        total_loan    += float(r.loan_amount or 0)
        total_balance += float(r.outstanding_balance or 0)

    blocks = [
        _section_block(i, g["label"], col_defs, mono_font, g["rows"], today, last_paid_map)
        for i, (_, g) in enumerate(groups.items(), 1)
    ]

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
{font_face}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:{body_font}; color:#1a1d23; font-size:9px; line-height:1.4; }}
table {{ border-collapse:collapse; width:100%; }}
</style>
</head><body>

<!-- IOP Header: centered brand, subtitle, pills -->
<div style="border-bottom:2px solid #1a1d23;padding-bottom:10px;margin-bottom:14px;">
  <p style="font-size:15px;font-weight:700;letter-spacing:0.5px;color:#1a1d23;margin-bottom:2px;text-align:center;">GG Finance</p>
  <p style="font-size:8px;font-weight:500;letter-spacing:1.5px;color:#555;margin-bottom:6px;text-align:center;">IOP · வட்டி வசூல் பட்டியல்</p>
  <table style="border:none;margin-left:auto;margin-right:auto;width:auto;">
    <tr>
      <td style="border:1px solid #ccc;border-radius:2px;padding:2px 8px;font-size:8.5px;color:#444;">{today_str}</td>
      <td style="border:none;width:12px;"></td>
      <td style="border:1px solid #ccc;border-radius:2px;padding:2px 8px;font-size:8.5px;color:#444;">{len(rows)} வாடிக்கையாளர்கள்</td>
    </tr>
  </table>
  <div style="height:1px;background:#ccc;margin-top:10px;"> </div>
</div>

{_layout_sections(blocks, two_col)}

{_grand_total_html(total_loan, total_balance, col_defs, mono_font)}

{_footer_html(
    f"GG Finance · IOP · வட்டி வசூல் பட்டியல் · {today_str}",
    f"{len(rows)} வாடிக்கையாளர்கள்",
    mono_font,
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
