"""Daily Print PDF — EDI (daily collection) and IOP (interest cadence).

Design reference: "My Investments & Savings.xlsx" — clean horizontal-line-only
spreadsheet look. No heavy cell grid; only subtle bottom borders per row.
Group headers use a left accent bar. Column header appears once (no thead repeat).

EDI:  Portrait A4. Groups sorted by segment. Columns: ID, Tamil name, loan,
      balance, days since last payment. Totals at bottom.

IOP:  Landscape A4. Current 10-day period columns with ✕ markers per customer.
"""
import calendar
import io
import logging
import os
import traceback
from datetime import date

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
# dk2=#44546A  lt2=#E7E6E6  accent6=#70AD47  borders=very light
_C_HDR_BG    = "#44546A"   # column header background (dk2)
_C_HDR_FG    = "#FFFFFF"
_C_HDR_LINE  = "#344859"   # bottom line under header row
_C_GRP_FG    = "#44546A"   # group-name text (dk2)
_C_GRP_BG    = "#F4F4F4"   # group-name row background (very light)
_C_GRP_LINE  = "#70AD47"   # left accent + bottom line on group rows (accent6)
_C_ROW_EVEN  = "#FFFFFF"
_C_ROW_ALT   = "#F9F9F9"
_C_ROW_LINE  = "#E8E8E8"   # subtle horizontal row separator
_C_TOTAL_BG  = "#E7E6E6"   # lt2 for total row
_C_TOTAL_LINE= "#BFBFBF"
_C_DATE      = "#44546A"
_C_MUTED     = "#7F7F7F"
_C_DUE_BG    = "#C6DEB5"   # accent6 tint+0.6 for due-day marker
_C_DUE_FG    = "#375523"   # accent6 tint-0.5


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
    archive  = fitz.Archive(_FONTS_DIR) if has_font else None
    story    = fitz.Story(html, archive=archive) if archive else fitz.Story(html)

    buf    = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    A4     = fitz.paper_rect("a4")
    page_rect = fitz.Rect(0, 0, A4.y1, A4.x1) if landscape else A4
    margin = 36
    clip   = fitz.Rect(
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
    return (
        f'@font-face {{ font-family:"NotoTamil"; src:url("{_TAMIL_FONT_FILE}"); }}',
        '"NotoTamil", sans-serif',
    )


def _base_css(body_font: str, font_face: str) -> str:
    return f"""
{font_face}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:{body_font}; font-size:12px; color:{_C_DATE}; line-height:1.45; }}
table {{ border-collapse:collapse; width:100%; }}
"""


# ── Header cell — only bottom border ──────────────────────────────────────────
def _th(content: str, extra: str = "") -> str:
    return (
        f'<th style="padding:7px 8px;background:{_C_HDR_BG};color:{_C_HDR_FG};'
        f'font-size:11px;font-weight:bold;border-bottom:2px solid {_C_HDR_LINE};{extra}">'
        f'{content}</th>'
    )


# ── Data cell — only bottom border ─────────────────────────────────────────────
def _td(content: str, extra: str = "", bg: str = "") -> str:
    bg_str = f"background:{bg};" if bg else ""
    return (
        f'<td style="padding:5px 8px;border-bottom:1px solid {_C_ROW_LINE};'
        f'{bg_str}{extra}">{content}</td>'
    )


# ── Group header row — left accent + bottom line ───────────────────────────────
def _grp_row(label: str, colspan: int) -> str:
    return (
        f'<tr>'
        f'<td colspan="{colspan}" style="padding:7px 10px;'
        f'background:{_C_GRP_BG};color:{_C_GRP_FG};font-weight:bold;font-size:11px;'
        f'border-left:3px solid {_C_GRP_LINE};border-bottom:1px solid {_C_GRP_LINE};">'
        f'{label}</td></tr>'
    )


# ── EDI daily print ────────────────────────────────────────────────────────────

@router.get("/edi")
def edi_daily_print(
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

    today     = date.today()
    today_str = today.strftime("%d-%m-%Y")

    has_font = os.path.exists(_TAMIL_FONT_PATH)
    font_face, body_font = _font_face_css(has_font)

    current_grp   = None
    total_loan    = 0.0
    total_balance = 0.0
    tbody         = ""
    row_num       = 0

    for r in rows:
        seg_id = r.customer_segment_id
        if seg_id != current_grp:
            current_grp = seg_id
            grp_label   = r.grp_ta or r.grp_en or f"Group {seg_id}"
            seg_num     = int(seg_id) if seg_id else "—"
            tbody += _grp_row(f"{seg_num}. {grp_label}", 6)

        loan      = float(r.loan_amount or 0)
        balance   = float(r.outstanding_balance or 0)
        total_loan    += loan
        total_balance += balance

        last_paid = r.last_paid
        if last_paid:
            days_ago  = (today - last_paid).days
            days_str  = str(days_ago)
            days_color = "#C0392B" if days_ago > 7 else (_C_MUTED if days_ago > 3 else _C_GRP_LINE)
        else:
            days_str   = "—"
            days_color = _C_MUTED

        row_bg = _C_ROW_EVEN if row_num % 2 == 0 else _C_ROW_ALT
        tbody += (
            f'<tr>'
            + _td(str(r.customer_id),        "text-align:center;color:{};font-size:11px;width:36px".format(_C_MUTED), row_bg)
            + _td(r.ta_name or "—",          "font-size:12px;", row_bg)
            + _td(_fmt_date(r.loan_start_date), f"text-align:center;font-size:10px;color:{_C_MUTED};width:78px", row_bg)
            + _td(_fmt_amt(loan),            "text-align:right;width:80px;", row_bg)
            + _td(_fmt_amt(balance),         "text-align:right;width:80px;font-weight:bold;", row_bg)
            + _td(days_str,                  f"text-align:center;font-size:12px;color:{days_color};font-weight:bold;width:40px", row_bg)
            + "</tr>"
        )
        row_num += 1

    tbody += (
        f'<tr style="background:{_C_TOTAL_BG};font-weight:bold;">'
        f'<td colspan="3" style="padding:6px 8px;border-top:2px solid {_C_TOTAL_LINE};'
        f'border-bottom:1px solid {_C_TOTAL_LINE};text-align:right;font-size:11px;">மொத்தம்</td>'
        f'<td style="padding:6px 8px;border-top:2px solid {_C_TOTAL_LINE};'
        f'border-bottom:1px solid {_C_TOTAL_LINE};text-align:right;">{_fmt_amt(total_loan)}</td>'
        f'<td style="padding:6px 8px;border-top:2px solid {_C_TOTAL_LINE};'
        f'border-bottom:1px solid {_C_TOTAL_LINE};text-align:right;">{_fmt_amt(total_balance)}</td>'
        f'<td style="padding:6px 8px;border-top:2px solid {_C_TOTAL_LINE};'
        f'border-bottom:1px solid {_C_TOTAL_LINE};"></td>'
        f'</tr>'
    )

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{_base_css(body_font, font_face)}</style>
</head><body>

<table style="margin-bottom:16px;border:none;">
  <tr>
    <td style="border:none;padding:0;vertical-align:bottom;">
      <div style="font-size:20px;font-weight:bold;color:{_C_HDR_BG};letter-spacing:-0.3px;">GG Finance</div>
      <div style="font-size:12px;color:{_C_MUTED};margin-top:3px;">EDI — தினசரி வசூல் பட்டியல்</div>
    </td>
    <td style="border:none;padding:0;text-align:right;vertical-align:bottom;">
      <div style="font-size:30px;font-weight:bold;color:{_C_DATE};letter-spacing:-0.5px;">{today_str}</div>
      <div style="font-size:11px;color:{_C_MUTED};margin-top:2px;">{len(rows)} வாடிக்கையாளர்கள்</div>
    </td>
  </tr>
</table>

<table>
  <tr>
    {_th("ID",              "text-align:center;width:36px;")}
    {_th("பெயர் (Tamil Name)")}
    {_th("தொடக்கம்",        "text-align:center;width:78px;")}
    {_th("கடன் ₹",          "text-align:right;width:80px;")}
    {_th("நிலுவை ₹",        "text-align:right;width:80px;")}
    {_th("நாட்கள்",         "text-align:center;width:40px;")}
  </tr>
  {tbody}
</table>

<p style="margin-top:12px;font-size:9px;color:{_C_MUTED};text-align:right;">
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
    d        = today.day
    last_day = calendar.monthrange(today.year, today.month)[1]
    m, y     = today.month, today.year

    if d <= 10:
        days  = list(range(1, 11))
        title = "முதல் பத்து நாட்கள்"
        rng   = f"01/{m:02d}/{y} — 10/{m:02d}/{y}"
    elif d <= 20:
        days  = list(range(11, 21))
        title = "இரண்டாவது பத்து நாட்கள்"
        rng   = f"11/{m:02d}/{y} — 20/{m:02d}/{y}"
    else:
        days  = list(range(21, last_day + 1))
        title = "மூன்றாவது பத்து நாட்கள்"
        rng   = f"21/{m:02d}/{y} — {last_day:02d}/{m:02d}/{y}"

    return days, title, rng


def _due_days_in_period(loan_start_day: int, freq: int, period_days: list[int]) -> set[int]:
    if freq <= 0:
        return set()
    if freq == 1:
        return set(period_days)
    due: set[int] = set()
    for d in range(1, 32):
        if (d - loan_start_day) % freq == 0:
            due.add(d)
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
    month_year  = today.strftime("%m-%Y")
    n_day_cols  = len(period_days)

    has_font = os.path.exists(_TAMIL_FONT_PATH)
    font_face, body_font = _font_face_css(has_font)

    day_headers = "".join(
        _th(str(d), "text-align:center;width:22px;font-size:10px;")
        for d in period_days
    )

    current_grp = None
    total_loan  = 0.0
    tbody       = ""
    row_num     = 0

    for r in rows:
        seg_id = r.customer_segment_id
        if seg_id != current_grp:
            current_grp = seg_id
            grp_label   = r.grp_ta or r.grp_en or f"Group {seg_id}"
            seg_num     = int(seg_id) if seg_id else "—"
            tbody += _grp_row(f"{seg_num}. {grp_label}", 3 + n_day_cols)

        loan = float(r.loan_amount or 0)
        total_loan += loan

        freq      = int(round(float(r.interest_payment_frequency or 30)))
        start_day = r.loan_start_date.day if r.loan_start_date else 1
        due_days  = _due_days_in_period(start_day, freq, period_days)

        name_cell = f"{freq}/ {r.ta_name}" if r.ta_name else str(freq)
        row_bg    = _C_ROW_EVEN if row_num % 2 == 0 else _C_ROW_ALT

        day_cells = ""
        for d in period_days:
            if d in due_days:
                day_cells += (
                    f'<td style="text-align:center;padding:4px 2px;width:22px;'
                    f'background:{_C_DUE_BG};color:{_C_DUE_FG};font-weight:bold;'
                    f'font-size:10px;border-bottom:1px solid {_C_ROW_LINE};">✕</td>'
                )
            else:
                day_cells += (
                    f'<td style="text-align:center;padding:4px 2px;width:22px;'
                    f'font-size:10px;color:#D0D0D0;border-bottom:1px solid {_C_ROW_LINE};">·</td>'
                )

        tbody += (
            f'<tr style="background:{row_bg};">'
            + _td(str(r.customer_id), f"text-align:center;font-size:10px;color:{_C_MUTED};width:28px", row_bg)
            + _td(name_cell,          "font-size:11px;min-width:105px;", row_bg)
            + _td(_fmt_amt(loan),     "text-align:right;font-size:11px;width:68px;", row_bg)
            + day_cells
            + "</tr>"
        )
        row_num += 1

    tbody += (
        f'<tr style="background:{_C_TOTAL_BG};font-weight:bold;">'
        f'<td colspan="2" style="padding:6px 8px;border-top:2px solid {_C_TOTAL_LINE};'
        f'border-bottom:1px solid {_C_TOTAL_LINE};text-align:right;font-size:11px;">மொத்தம்</td>'
        f'<td style="padding:6px 8px;border-top:2px solid {_C_TOTAL_LINE};'
        f'border-bottom:1px solid {_C_TOTAL_LINE};text-align:right;">{_fmt_amt(total_loan)}</td>'
        f'<td colspan="{n_day_cols}" style="border-top:2px solid {_C_TOTAL_LINE};'
        f'border-bottom:1px solid {_C_TOTAL_LINE};"></td>'
        f'</tr>'
    )

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{_base_css(body_font, font_face)}</style>
</head><body>

<table style="margin-bottom:14px;border:none;">
  <tr>
    <td style="border:none;padding:0;vertical-align:bottom;">
      <div style="font-size:18px;font-weight:bold;color:{_C_HDR_BG};">GG Finance</div>
      <div style="font-size:11px;color:{_C_MUTED};margin-top:3px;">IOP — வட்டி வசூல் பட்டியல்</div>
      <div style="font-size:10px;color:{_C_MUTED};margin-top:2px;">{period_range}</div>
    </td>
    <td style="border:none;padding:0;text-align:right;vertical-align:bottom;">
      <div style="font-size:26px;font-weight:bold;color:{_C_DATE};">{month_year}</div>
      <div style="font-size:11px;color:{_C_MUTED};margin-top:2px;">{period_title}</div>
      <div style="font-size:10px;color:{_C_MUTED};">{len(rows)} வாடிக்கையாளர்கள்</div>
    </td>
  </tr>
</table>

<table>
  <tr>
    {_th("ID",           "text-align:center;width:28px;")}
    {_th("பெயர் / Freq")}
    {_th("கடன் ₹",       "text-align:right;width:68px;")}
    {day_headers}
  </tr>
  {tbody}
</table>

<p style="margin-top:10px;font-size:9px;color:{_C_MUTED};text-align:right;">
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
