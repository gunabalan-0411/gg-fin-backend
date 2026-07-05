"""Daily Print PDF — EDI (daily collection) and IOP (interest cadence).

EDI:  Portrait A4. Groups sorted by segment. Shows ID, Tamil name, loan, balance,
      days since last payment. Totals at bottom.

IOP:  Landscape A4. Shows the current 10-day payment period with per-customer due
      day markers (✕). Groups sorted by segment.
"""
import calendar
import io
import logging
import os
import traceback
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import text
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session

router = APIRouter()
log = logging.getLogger(__name__)

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "fonts")
_TAMIL_FONT_FILE = "NotoSansTamil-Regular.ttf"
_TAMIL_FONT_PATH = os.path.join(_FONTS_DIR, _TAMIL_FONT_FILE)

# ── Colours — "My Investments & Savings.xlsx" Office theme ────────────────────
# dk2=#44546A  lt2=#E7E6E6  accent3=#A5A5A5  accent6=#70AD47
_C_HEADER_BG   = "#44546A"   # dk2 — dark steel-blue column-header row
_C_HEADER_FG   = "#FFFFFF"
_C_GRP_BG      = "#70AD47"   # accent6 — Office green group-name rows
_C_GRP_FG      = "#FFFFFF"
_C_ROW_ALT     = "#F2F2F2"   # lt2 slightly darkened
_C_ROW_EVEN    = "#FFFFFF"
_C_BORDER      = "#BFBFBF"   # thin gray (dk1 tint -0.25)
_C_TOTAL_BG    = "#E7E6E6"   # lt2 — very light warm gray total row
_C_DATE        = "#44546A"   # dk2 for headings
_C_MUTED       = "#7F7F7F"   # muted text
_C_DUE_BG      = "#C6DEB5"   # accent6 tint +0.60 — light green
_C_DUE_FG      = "#375523"   # accent6 tint -0.50 — dark green


# ── Helpers ────────────────────────────────────────────────────────────────────

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


def _render_pdf(html: str, landscape: bool = False) -> bytes:
    import fitz

    has_font = os.path.exists(_TAMIL_FONT_PATH)
    archive = fitz.Archive(_FONTS_DIR) if has_font else None
    story = fitz.Story(html, archive=archive) if archive else fitz.Story(html)

    buf    = io.BytesIO()
    writer = fitz.DocumentWriter(buf)

    A4     = fitz.paper_rect("a4")
    if landscape:
        page_rect = fitz.Rect(0, 0, A4.y1, A4.x1)   # swap W/H
    else:
        page_rect = A4

    margin = 32
    clip = fitz.Rect(
        page_rect.x0 + margin, page_rect.y0 + margin,
        page_rect.x1 - margin, page_rect.y1 - margin,
    )

    more = True
    while more:
        device = writer.begin_page(page_rect)
        more, _ = story.place(clip)
        story.draw(device)
        writer.end_page()

    writer.close()
    return buf.getvalue()


def _font_face_css(has_font: bool) -> tuple[str, str]:
    if not has_font:
        return "", "sans-serif"
    face = f'@font-face {{ font-family:"NotoTamil"; src:url("{_TAMIL_FONT_FILE}"); }}'
    return face, '"NotoTamil", sans-serif'


def _td(content: str, style: str = "") -> str:
    return f'<td style="padding:5px 7px;border:1px solid {_C_BORDER};{style}">{content}</td>'


def _th(content: str, style: str = "") -> str:
    return (
        f'<th style="padding:6px 7px;border:1px solid {_C_BORDER};'
        f'background:{_C_HEADER_BG};color:{_C_HEADER_FG};font-size:11px;'
        f'font-weight:bold;{style}">{content}</th>'
    )


# ── EDI daily print ────────────────────────────────────────────────────────────

@router.get("/edi")
def edi_daily_print(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    import fitz  # noqa: F401 — validates fitz is available

    rows = session.exec(text("""
        SELECT
            c.customer_id,
            c.customer_segment_id,
            c.loan_amount,
            c.outstanding_balance,
            c.loan_start_date,
            COALESCE(nm.customer_name_ta, c.customer_name, '') AS ta_name,
            COALESCE(gm.customer_segment_name_ta, gm.customer_segment_name_en, '') AS grp_ta,
            COALESCE(gm.customer_segment_name_en, '') AS grp_en,
            (
                SELECT MAX(t.collection_date)
                FROM tbl_edi_transactions t
                WHERE t.customer_id = c.customer_id AND t.payment_status = 'PAID'
            ) AS last_paid
        FROM tbl_edi_customer c
        LEFT JOIN tbl_edi_name_map nm  ON nm.customer_id = c.customer_id
        LEFT JOIN tbl_edi_group_map gm ON gm.customer_segment_id = c.customer_segment_id
        WHERE c.is_closed = false AND COALESCE(c.ignore, false) = false
        ORDER BY c.customer_segment_id ASC NULLS LAST, c.loan_start_date ASC, c.customer_id ASC
    """)).fetchall()

    today = date.today()
    today_str = today.strftime("%d-%m-%Y")

    has_font = os.path.exists(_TAMIL_FONT_PATH)
    font_face, body_font = _font_face_css(has_font)

    # Build table rows
    tbody = ""
    current_grp = None
    total_loan = 0.0
    total_balance = 0.0
    row_num = 0

    for r in rows:
        seg_id = r.customer_segment_id
        if seg_id != current_grp:
            current_grp = seg_id
            grp_label = r.grp_ta or r.grp_en or f"Group {seg_id}"
            seg_num = int(seg_id) if seg_id else "—"
            tbody += (
                f'<tr style="background:{_C_GRP_BG}">'
                f'<td colspan="5" style="padding:7px 10px;border:1px solid {_C_BORDER};'
                f'font-weight:bold;font-size:12px;color:{_C_GRP_FG}">'
                f'{seg_num}. {grp_label}</td></tr>'
            )

        loan      = float(r.loan_amount or 0)
        balance   = float(r.outstanding_balance or 0)
        total_loan    += loan
        total_balance += balance

        last_paid = r.last_paid
        if last_paid:
            days_ago = (today - last_paid).days
            days_str = str(days_ago)
            days_color = "#DC2626" if days_ago > 7 else (_C_MUTED if days_ago > 3 else _C_GRP_BG)
        else:
            days_str  = "—"
            days_color = _C_MUTED

        row_bg = _C_ROW_EVEN if row_num % 2 == 0 else _C_ROW_ALT
        tbody += (
            f'<tr style="background:{row_bg}">'
            + _td(str(r.customer_id), "text-align:center;font-size:11px;color:#6B7280;width:32px")
            + _td(r.ta_name or "—", "font-size:12px;min-width:120px")
            + _td(_fmt_date(r.loan_start_date), "text-align:center;font-size:11px;color:#6B7280;width:80px")
            + _td(_fmt_amt(loan), "text-align:right;font-size:12px;width:80px")
            + _td(_fmt_amt(balance), f"text-align:right;font-size:12px;font-weight:bold;width:80px")
            + _td(days_str, f"text-align:center;font-size:12px;color:{days_color};width:36px")
            + "</tr>"
        )
        row_num += 1

    # Total row
    tbody += (
        f'<tr style="background:{_C_TOTAL_BG};font-weight:bold">'
        + _td("மொத்தம்", f"colspan=3;font-size:12px;text-align:right;border:1px solid {_C_BORDER}")
        + _td(_fmt_amt(total_loan),    "text-align:right;font-size:12px;font-weight:bold")
        + _td(_fmt_amt(total_balance), "text-align:right;font-size:12px;font-weight:bold")
        + _td("", "")
        + "</tr>"
    )

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
{font_face}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:{body_font}; color:{_C_DATE}; font-size:13px; line-height:1.4; }}
table {{ border-collapse:collapse; width:100%; }}
</style>
</head><body>

<table style="margin-bottom:14px;border:none">
  <tr>
    <td style="border:none;padding:0;vertical-align:bottom">
      <div style="font-size:18px;font-weight:bold;color:{_C_HEADER_BG}">GG Finance</div>
      <div style="font-size:13px;color:{_C_MUTED}">EDI — தினசரி வசூல் பட்டியல்</div>
    </td>
    <td style="border:none;padding:0;text-align:right;vertical-align:bottom">
      <div style="font-size:26px;font-weight:bold;color:{_C_DATE}">{today_str}</div>
      <div style="font-size:11px;color:{_C_MUTED}">{len(rows)} வாடிக்கையாளர்கள்</div>
    </td>
  </tr>
</table>

<table>
  <thead>
    <tr>
      {_th("ID", "text-align:center;width:32px")}
      {_th("பெயர் (Tamil Name)", "min-width:120px")}
      {_th("தொடக்கம்", "text-align:center;width:80px")}
      {_th("கடன் ₹", "text-align:right;width:80px")}
      {_th("நிலுவை ₹", "text-align:right;width:80px")}
      {_th("நாட்கள்", "text-align:center;width:36px")}
    </tr>
  </thead>
  <tbody>{tbody}</tbody>
</table>

<p style="margin-top:10px;font-size:9px;color:{_C_MUTED};text-align:right">
  GG Finance · EDI Daily Print · {today_str}
</p>
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


# ── IOP interest cadence print ─────────────────────────────────────────────────

def _iop_period(today: date) -> tuple[list[int], str, str]:
    """Return (period_days, title_ta, range_str) for today's 10-day period."""
    d = today.day
    last_day = calendar.monthrange(today.year, today.month)[1]

    if d <= 10:
        period_days = list(range(1, 11))
        title = "முதல் பத்து நாட்கள்"
        range_str = f"01/{today.month:02d}/{today.year} — 10/{today.month:02d}/{today.year}"
    elif d <= 20:
        period_days = list(range(11, 21))
        title = "இரண்டாவது பத்து நாட்கள்"
        range_str = f"11/{today.month:02d}/{today.year} — 20/{today.month:02d}/{today.year}"
    else:
        period_days = list(range(21, last_day + 1))
        title = "மூன்றாவது பத்து நாட்கள்"
        range_str = f"21/{today.month:02d}/{today.year} — {last_day:02d}/{today.month:02d}/{today.year}"

    return period_days, title, range_str


def _due_days_in_period(loan_start_day: int, freq: int, period_days: list[int]) -> set[int]:
    """Return which days in period_days are payment days for this customer."""
    if freq <= 0:
        return set()
    if freq == 1:
        return set(period_days)
    due: set[int] = set()
    for d in range(1, 32):
        if (d - loan_start_day) % freq == 0:
            due.add(d)
    # 1 and 31 are same (month boundary normalisation)
    if 1 in due and 31 in due:
        due.discard(31)
    return due & set(period_days)


@router.get("/iop")
def iop_daily_print(
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
            c.interest_payment_frequency,
            COALESCE(nm.customer_name_ta, c.customer_name, '') AS ta_name,
            COALESCE(gm.customer_segment_name_ta, gm.customer_segment_name_en, '') AS grp_ta,
            COALESCE(gm.customer_segment_name_en, '') AS grp_en
        FROM tbl_iop_customer c
        LEFT JOIN tbl_iop_name_map nm  ON nm.customer_id = c.customer_id
        LEFT JOIN tbl_iop_group_map gm ON gm.customer_segment_id = c.customer_segment_id
        WHERE c.is_closed = false AND COALESCE(c.ignore, false) = false
        ORDER BY c.customer_segment_id ASC NULLS LAST, c.loan_start_date ASC, c.customer_id ASC
    """)).fetchall()

    today = date.today()
    period_days, period_title, period_range = _iop_period(today)
    month_year = today.strftime("%m-%Y")

    has_font = os.path.exists(_TAMIL_FONT_PATH)
    font_face, body_font = _font_face_css(has_font)

    # Day column headers
    day_headers = "".join(
        _th(str(d), "text-align:center;width:22px;font-size:10px")
        for d in period_days
    )

    # Table body
    tbody = ""
    current_grp = None
    total_loan  = 0.0
    row_num = 0
    n_day_cols = len(period_days)

    for r in rows:
        seg_id = r.customer_segment_id
        if seg_id != current_grp:
            current_grp = seg_id
            grp_label = r.grp_ta or r.grp_en or f"Group {seg_id}"
            seg_num   = int(seg_id) if seg_id else "—"
            tbody += (
                f'<tr style="background:{_C_GRP_BG}">'
                f'<td colspan="{3 + n_day_cols}" style="padding:6px 10px;border:1px solid {_C_BORDER};'
                f'font-weight:bold;font-size:11px;color:{_C_GRP_FG}">'
                f'{seg_num}. {grp_label}</td></tr>'
            )

        loan     = float(r.loan_amount or 0)
        total_loan += loan

        freq = int(round(float(r.interest_payment_frequency or 30)))
        start_day = r.loan_start_date.day if r.loan_start_date else 1
        due_days  = _due_days_in_period(start_day, freq, period_days)

        # Tamil name prefixed with frequency
        name_cell = f"{freq}/ {r.ta_name}" if r.ta_name else str(freq)

        row_bg = _C_ROW_EVEN if row_num % 2 == 0 else _C_ROW_ALT
        day_cells = ""
        for d in period_days:
            if d in due_days:
                day_cells += (
                    f'<td style="text-align:center;padding:4px 2px;border:1px solid {_C_BORDER};'
                    f'background:{_C_DUE_BG};color:{_C_DUE_FG};font-weight:bold;font-size:10px;width:22px">✕</td>'
                )
            else:
                day_cells += (
                    f'<td style="text-align:center;padding:4px 2px;border:1px solid {_C_BORDER};'
                    f'font-size:10px;width:22px;color:#D5D5D5">·</td>'
                )

        tbody += (
            f'<tr style="background:{row_bg}">'
            + _td(str(r.customer_id), "text-align:center;font-size:10px;color:#6B7280;width:28px")
            + _td(name_cell,          "font-size:11px;min-width:110px")
            + _td(_fmt_amt(loan),     "text-align:right;font-size:11px;width:70px")
            + day_cells
            + "</tr>"
        )
        row_num += 1

    # Total row
    tbody += (
        f'<tr style="background:{_C_TOTAL_BG};font-weight:bold">'
        + _td("மொத்தம்", f"colspan=2;text-align:right;font-size:11px;border:1px solid {_C_BORDER}")
        + _td(_fmt_amt(total_loan), "text-align:right;font-size:11px;font-weight:bold")
        + f'<td colspan="{n_day_cols}" style="border:1px solid {_C_BORDER}"></td>'
        + "</tr>"
    )

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
{font_face}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:{body_font}; color:{_C_DATE}; font-size:12px; line-height:1.4; }}
table {{ border-collapse:collapse; width:100%; }}
</style>
</head><body>

<table style="margin-bottom:12px;border:none">
  <tr>
    <td style="border:none;padding:0;vertical-align:bottom">
      <div style="font-size:17px;font-weight:bold;color:{_C_HEADER_BG}">GG Finance</div>
      <div style="font-size:12px;color:{_C_MUTED}">IOP — வட்டி வசூல் பட்டியல்</div>
      <div style="font-size:11px;color:{_C_MUTED};margin-top:3px">{period_range}</div>
    </td>
    <td style="border:none;padding:0;text-align:right;vertical-align:bottom">
      <div style="font-size:24px;font-weight:bold;color:{_C_DATE}">{month_year}</div>
      <div style="font-size:12px;color:{_C_MUTED};margin-top:2px">{period_title}</div>
      <div style="font-size:10px;color:{_C_MUTED}">{len(rows)} வாடிக்கையாளர்கள்</div>
    </td>
  </tr>
</table>

<table>
  <thead>
    <tr>
      {_th("ID",           "text-align:center;width:28px")}
      {_th("பெயர் / Freq", "min-width:110px")}
      {_th("கடன் ₹",       "text-align:right;width:70px")}
      {day_headers}
    </tr>
  </thead>
  <tbody>{tbody}</tbody>
</table>

<p style="margin-top:8px;font-size:9px;color:{_C_MUTED};text-align:right">
  GG Finance · IOP Interest Print · {period_range}
</p>
</body></html>"""

    try:
        pdf_bytes = _render_pdf(html, landscape=True)
    except Exception:
        log.error("IOP PDF render failed:\n%s", traceback.format_exc())
        raise HTTPException(500, "PDF generation failed")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="IOP_Interest_{month_year}.pdf"'},
    )
