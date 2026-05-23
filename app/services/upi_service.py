"""
UPI transaction service — Gmail OAuth, email parsing, CSV parsing.
"""
from __future__ import annotations

import base64
import io
import logging
import re
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

logger = logging.getLogger(__name__)

from sqlmodel import Session, select

from app.core.config import settings
from app.models.upi import GmailSettings, UpiTransaction

# ── Regex patterns ────────────────────────────────────────────────────────────

# HDFC credit SMS/email pattern
# e.g. "Rs. 500.00 is successfully credited to your account **2371 by VPA
#       sakthiveljilla23184-5@okicici R Sakthivel on 13-03-26.
#       Your UPI transaction reference number is 643818139861."
_EMAIL_CREDIT = re.compile(
    r"Rs\.?\s*([\d,]+\.?\d*)\s+is\s+successfully\s+credited"
    r".+?by\s+VPA\s+(\S+)\s+(.+?)\s+on\s+(\d{2}-\d{2}-\d{2})"
    r".+?reference\s+number\s+is\s+(\d+)",
    re.IGNORECASE | re.DOTALL,
)

# HDFC CSV narration pattern
# e.g. UPI-R THIYAGARAJAN-THIYAGARAJANTHIYAGU281@OKAXIS-UCBA0000430-524268033144-UPI
# e.g. UPI-G GUNABALAN-GUNABALAN0411-1@OKICICI-ICIC0000009-524239354696-UPI
# e.g. UPI-MR MANIMOZHI GNANASE-SARATHKUMAR321G-2@OKAXIS-IDIB000S106-606941675992-BLANCE 5000
_CSV_NARRATION = re.compile(
    r"^UPI-(.+?)-([A-Z0-9\-]+@[A-Z0-9]+)-[A-Z0-9]+-(\d{6,})(?:-(.*))?$",
    re.IGNORECASE,
)


def _parse_date_ddmmyy(s: str) -> date:
    """Parse dd-mm-yy or dd/mm/yy into a date object."""
    s = s.strip().replace("/", "-")
    parts = s.split("-")
    day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
    if year < 100:
        year += 2000
    return date(year, month, day)


def _to_decimal(s: str) -> Decimal:
    return Decimal(str(s).replace(",", "").strip())


# ── Gmail OAuth ───────────────────────────────────────────────────────────────
#
# We use requests_oauthlib directly (no PKCE) and exchange the code manually
# via a plain HTTP POST so there is no code_verifier mismatch across requests.

_GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid",
    "email",
]
_GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
_GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


def get_auth_url(redirect_uri: str, frontend_origin: str = "") -> str:
    """Build the Google OAuth consent URL without PKCE.

    frontend_origin is embedded in the state so the callback knows where to
    redirect the browser after the code exchange, regardless of env config.
    """
    import os
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # allow http on localhost

    from requests_oauthlib import OAuth2Session  # type: ignore

    oauth = OAuth2Session(
        client_id=settings.GOOGLE_CLIENT_ID,
        redirect_uri=redirect_uri,
        scope=_GMAIL_SCOPES,
    )
    auth_url, _ = oauth.authorization_url(
        _GOOGLE_AUTH_URI,
        access_type="offline",
        prompt="consent",
        state=frontend_origin or "default",
    )
    return auth_url


def exchange_code(code: str, redirect_uri: str, session: Session) -> str:
    """Exchange OAuth code for tokens via plain POST (no PKCE), persist to DB."""
    import requests as http_requests  # plain requests, no PKCE
    from datetime import timezone as tz

    resp = http_requests.post(
        _GOOGLE_TOKEN_URI,
        data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    token_data = resp.json()

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token") or None
    expires_in = token_data.get("expires_in", 3600)
    expiry = datetime.now(tz.utc) + timedelta(seconds=expires_in)

    # Fetch user email via userinfo endpoint
    ui_resp = http_requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    email = ui_resp.json().get("email", "") if ui_resp.ok else ""

    g = session.get(GmailSettings, 1) or GmailSettings(id=1)
    g.email = email
    g.access_token = access_token
    g.refresh_token = refresh_token
    g.token_expiry = expiry
    session.add(g)
    session.commit()
    return email


def get_gmail_status(session: Session) -> dict:
    g = session.get(GmailSettings, 1)
    if not g or not g.access_token:
        return {"connected": False, "email": None}
    return {"connected": True, "email": g.email}


def disconnect_gmail(session: Session) -> None:
    g = session.get(GmailSettings, 1)
    if g:
        g.access_token = None
        g.refresh_token = None
        g.token_expiry = None
        session.add(g)
        session.commit()


def _build_gmail_service(g: GmailSettings, session: Session):
    try:
        from google.oauth2.credentials import Credentials  # type: ignore
        from google.auth.transport.requests import Request  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
    except ImportError:
        raise RuntimeError("google-api-python-client is not installed.")

    expiry = g.token_expiry
    if expiry is not None and expiry.tzinfo is not None:
        expiry = expiry.replace(tzinfo=None)  # google-auth expects naive UTC

    creds = Credentials(
        token=g.access_token,
        refresh_token=g.refresh_token,
        token_uri=_GOOGLE_TOKEN_URI,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        expiry=expiry,
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        g.access_token = creds.token
        g.token_expiry = creds.expiry
        session.add(g)
        session.commit()

    return build("gmail", "v1", credentials=creds)


# ── Gmail sync ────────────────────────────────────────────────────────────────

def sync_gmail(session: Session) -> dict:
    """Fetch HDFC credit emails from last 1 year and import UPI transactions."""
    g = session.get(GmailSettings, 1)
    if not g or not g.access_token:
        raise ValueError("Gmail not connected.")

    gmail = _build_gmail_service(g, session)

    # Fetch HDFC credit emails from last 1 year
    after_date = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y/%m/%d")
    query = f"from:hdfcbank credited after:{after_date}"

    results = gmail.users().messages().list(
        userId="me", q=query, maxResults=500
    ).execute()

    messages = results.get("messages", [])
    logger.info("Gmail sync: found %d messages for query: %s", len(messages), query)
    imported = 0
    skipped = 0

    for msg_ref in messages:
        msg = gmail.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()

        body = _extract_body(msg)
        if not body:
            logger.warning("MSG %s: no plain-text body found, skipping", msg_ref["id"])
            skipped += 1
            continue

        logger.debug("MSG %s body snippet:\n%s", msg_ref["id"], body[:300])

        txn = _parse_email_credit(body)
        if not txn:
            logger.warning(
                "MSG %s: regex did not match. Body (first 400 chars):\n%s",
                msg_ref["id"], body[:400],
            )
            skipped += 1
            continue

        existing = session.exec(
            select(UpiTransaction).where(UpiTransaction.upi_ref_no == txn.upi_ref_no)
        ).first()
        if existing:
            logger.info("MSG %s: ref %s already exists, skipping", msg_ref["id"], txn.upi_ref_no)
            skipped += 1
            continue

        logger.info("MSG %s: importing ref %s amount %s", msg_ref["id"], txn.upi_ref_no, txn.amount)
        session.add(txn)
        imported += 1

    session.commit()
    return {"imported": imported, "skipped": skipped, "total_found": len(messages)}


def _extract_body(msg: dict) -> str:
    """Extract text body from Gmail message (plain text preferred, HTML fallback)."""
    payload = msg.get("payload", {})
    html_fallback = ""

    def _walk(part) -> str:
        nonlocal html_fallback
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")
        if mime == "text/plain" and data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
        if mime == "text/html" and data and not html_fallback:
            raw = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
            html_fallback = re.sub(r"<[^>]+>", " ", raw)
        for sub in part.get("parts", []):
            result = _walk(sub)
            if result:
                return result
        return ""

    plain = _walk(payload)
    return plain or html_fallback


def _parse_email_credit(body: str) -> Optional[UpiTransaction]:
    m = _EMAIL_CREDIT.search(body)
    if not m:
        return None
    try:
        amount = _to_decimal(m.group(1))
        vpa = m.group(2).strip()
        name = m.group(3).strip()
        txn_date = _parse_date_ddmmyy(m.group(4))
        ref_no = m.group(5).strip()
        return UpiTransaction(
            upi_ref_no=ref_no,
            amount=amount,
            transaction_type="credit",
            sender_vpa=vpa,
            sender_name=name,
            transaction_date=txn_date,
            source="gmail",
        )
    except (InvalidOperation, ValueError, IndexError):
        return None


# ── CSV import ────────────────────────────────────────────────────────────────

def import_csv(content: bytes, session: Session) -> dict:
    """Parse HDFC bank statement XLS/XLSX and import credit (deposit) transactions."""
    try:
        import pandas as pd  # type: ignore
    except ImportError:
        raise RuntimeError("pandas is not installed.")

    buf = io.BytesIO(content)
    try:
        # Try XLS (older HDFC format) first, then XLSX
        try:
            df = pd.read_excel(buf, engine="xlrd", dtype=str)
        except Exception:
            buf.seek(0)
            df = pd.read_excel(buf, engine="openpyxl", dtype=str)
    except Exception as e:
        raise ValueError(f"Could not read file as XLS/XLSX: {e}")

    # Normalize column names
    df.columns = [c.strip() for c in df.columns]

    # Identify required columns
    col_map = {}
    for col in df.columns:
        lc = col.lower()
        if "narration" in lc:
            col_map["narration"] = col
        elif "deposit" in lc:
            col_map["deposit"] = col
        elif "date" in lc and "value" not in lc and "narration" not in lc:
            col_map["date"] = col
        elif "ref" in lc or "chq" in lc:
            col_map["ref"] = col

    required = {"narration", "deposit", "date"}
    missing = required - set(col_map.keys())
    if missing:
        raise ValueError(f"CSV missing columns: {missing}. Found: {list(df.columns)}")

    imported = 0
    skipped = 0
    errors = 0

    for _, row in df.iterrows():
        deposit_raw = str(row.get(col_map["deposit"], "")).strip()
        if not deposit_raw or deposit_raw.lower() in ("nan", "", "0"):
            skipped += 1
            continue  # debit row

        narration = str(row.get(col_map["narration"], "")).strip()
        if not narration.upper().startswith("UPI"):
            skipped += 1
            continue

        date_raw = str(row.get(col_map["date"], "")).strip()
        ref_raw = str(row.get(col_map.get("ref", ""), "")).strip().lstrip("0") or None

        try:
            txn_date = _parse_date_ddmmyy(date_raw)
            amount = _to_decimal(deposit_raw)
        except (ValueError, InvalidOperation):
            errors += 1
            continue

        vpa, name, ref_no, notes = _parse_csv_narration(narration, ref_raw)
        if not ref_no:
            errors += 1
            continue

        existing = session.exec(
            select(UpiTransaction).where(UpiTransaction.upi_ref_no == ref_no)
        ).first()
        if existing:
            skipped += 1
            continue

        session.add(UpiTransaction(
            upi_ref_no=ref_no,
            amount=amount,
            sender_vpa=vpa,
            sender_name=name,
            notes=notes,
            transaction_date=txn_date,
            source="csv",
        ))
        imported += 1

    session.commit()
    return {"imported": imported, "skipped": skipped, "errors": errors}


def _parse_csv_narration(narration: str, fallback_ref: Optional[str]) -> tuple[str, str, str, Optional[str]]:
    """Returns (vpa, name, ref_no, notes)."""
    m = _CSV_NARRATION.match(narration.strip())
    if m:
        notes_raw = m.group(4)
        notes = notes_raw.strip() if notes_raw and notes_raw.strip().upper() != "UPI" else None
        return m.group(2).strip(), m.group(1).strip(), m.group(3).strip(), notes

    # Fallback: manually parse UPI-{name}-{vpa}-{bank}-{ref}-{notes}
    if narration.upper().startswith("UPI-"):
        rest = narration[4:]
        name_end = rest.find("-")
        if name_end != -1:
            name = rest[:name_end].strip()
            after_name = rest[name_end + 1:]
            at_pos = after_name.find("@")
            if at_pos != -1:
                dash_after_bank = after_name.find("-", at_pos)
                if dash_after_bank != -1:
                    vpa = after_name[:dash_after_bank].strip()
                    rest2 = after_name[dash_after_bank + 1:]
                    ref_match = re.search(r"\b(\d{6,})\b", rest2)
                    ref_no = ref_match.group(1) if ref_match else (fallback_ref or "")
                    if ref_match:
                        notes_raw = rest2[ref_match.end():].strip("-").strip()
                        notes = notes_raw if notes_raw and notes_raw.upper() != "UPI" else None
                    else:
                        notes = None
                    return vpa, name, ref_no, notes
                else:
                    vpa = after_name.strip()
                    return vpa, name, fallback_ref or "", None

    return "", narration, fallback_ref or "", None
