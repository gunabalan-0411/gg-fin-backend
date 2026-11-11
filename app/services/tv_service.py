"""
Home TV: pairing, device tokens, and the read-only customer search / detail the TV shows.

Pairing (like signing in to a streaming app on a TV):
    1. the TV asks for a code (`start_pairing`) and shows it; it keeps a secret `poll_id` to itself
    2. a logged-in person types the code on their phone / laptop (`approve_pairing`)
    3. the TV polls (`poll_pairing`); the first poll after approval gets a device token - once, then the pairing is spent

Only SHA-256 hashes of the poll secret and of the device token are stored. A token never expires but can be revoked.
"""
import hashlib
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.customer import EdiCustomer, IopCustomer
from app.models.customer_mapping import CustomerAlias
from app.models.mapping import EdiNameMap, IopNameMap
from app.models.transaction import EdiTransaction, IopTransaction
from app.models.tv import TvDevice, TvPairing
from app.utils.name_matching import get_similar_score

CODE_TTL = timedelta(minutes=10)
TOKEN_PREFIX = "ggtv_"
SEEN_EVERY = timedelta(minutes=5)       # last_seen_at is refreshed at most this often (one write per request would be wasteful)
MIN_SCORE = 60.0                        # the same bar the Voice Entry page uses for "worth offering"
MAX_RESULTS = 5
DEFAULT_NAME = "TV"
PRODUCTS = ("edi", "iop")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean_code(code: str) -> str:
    return re.sub(r"\D", "", code or "")


# ── Pairing ────────────────────────────────────────────────────────────────────────────────────────────────

def start_pairing(session: Session) -> dict:
    now = _now()
    # tidy: spent / expired pairings older than an hour are of no use to anyone
    for old in session.exec(select(TvPairing).where(TvPairing.expires_at < now - timedelta(hours=1))).all():
        session.delete(old)
    live = {
        p.code for p in session.exec(
            select(TvPairing).where(TvPairing.expires_at >= now, TvPairing.claimed_at == None)  # noqa: E711
        ).all()
    }
    rng = secrets.SystemRandom()
    code = ""
    for _ in range(50):
        code = f"{rng.randrange(0, 1_000_000):06d}"
        if code not in live:
            break
    poll_id = secrets.token_urlsafe(24)
    session.add(TvPairing(code=code, poll_hash=_hash(poll_id), expires_at=now + CODE_TTL))
    session.commit()
    return {"code": code, "poll_id": poll_id, "expires_in": int(CODE_TTL.total_seconds())}


def approve_pairing(session: Session, user_id: int, code: str, name: Optional[str]) -> Optional[TvPairing]:
    """None when the code is unknown, expired, or already used."""
    digits = _clean_code(code)
    if len(digits) != 6:
        return None
    now = _now()
    pairing = session.exec(
        select(TvPairing).where(
            TvPairing.code == digits, TvPairing.expires_at >= now,
            TvPairing.approved_user_id == None, TvPairing.claimed_at == None,  # noqa: E711
        )
    ).first()
    if not pairing:
        return None
    pairing.approved_user_id = user_id
    pairing.device_name = (name or "").strip()[:60] or DEFAULT_NAME
    pairing.approved_at = now
    session.add(pairing)
    session.commit()
    session.refresh(pairing)
    return pairing


def poll_pairing(session: Session, poll_id: str) -> dict:
    pairing = session.exec(select(TvPairing).where(TvPairing.poll_hash == _hash(poll_id or ""))).first()
    now = _now()
    if not pairing or pairing.claimed_at is not None or pairing.expires_at < now:
        return {"status": "expired"}
    if pairing.approved_user_id is None:
        return {"status": "pending"}
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    device = TvDevice(user_id=pairing.approved_user_id, name=pairing.device_name or DEFAULT_NAME, token_hash=_hash(token))
    session.add(device)
    pairing.claimed_at = now
    session.add(pairing)
    session.commit()
    return {"status": "approved", "token": token, "name": device.name}


# ── Devices ────────────────────────────────────────────────────────────────────────────────────────────────

def authenticate_device(session: Session, token: Optional[str]) -> Optional[TvDevice]:
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    device = session.exec(select(TvDevice).where(TvDevice.token_hash == _hash(token))).first()
    if not device or device.revoked_at is not None:
        return None
    now = _now()
    if device.last_seen_at is None or now - device.last_seen_at > SEEN_EVERY:
        device.last_seen_at = now
        session.add(device)
        session.commit()
    return device


def list_devices(session: Session, user_id: int) -> list[TvDevice]:
    return list(session.exec(
        select(TvDevice).where(TvDevice.user_id == user_id, TvDevice.revoked_at == None)  # noqa: E711
        .order_by(TvDevice.created_at.desc(), TvDevice.id.desc())
    ).all())


def revoke_device(session: Session, user_id: int, device_id: int) -> bool:
    device = session.get(TvDevice, device_id)
    if not device or device.user_id != user_id or device.revoked_at is not None:
        return False
    device.revoked_at = _now()
    session.add(device)
    session.commit()
    return True


# ── Read-only customer data for the TV ──────────────────────────────────────────────────────────────────────

def _models(product: str):
    if product == "edi":
        return EdiCustomer, EdiNameMap, EdiTransaction
    return IopCustomer, IopNameMap, IopTransaction


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _num(value) -> float:
    return float(value) if value is not None else 0.0


def search(session: Session, text: str, product: Optional[str] = None) -> list[dict]:
    """Active customers ranked by the Voice Entry matcher (English + Tamil name + 2nd aliases); a phone number finds by number.
    `product` (edi | iop) limits the search to one product - the TV's EL / தவணை switch; None searches both."""
    text = (text or "").strip()
    if not text:
        return []
    digits_only = re.sub(r"\D", "", text)
    phone_query = bool(re.fullmatch(r"[\d\s+\-]+", text))

    customers: dict[str, dict] = {}        # "edi:12" -> what we need to show
    candidates: list[dict] = []
    for prod in ((product,) if product in PRODUCTS else PRODUCTS):
        Customer, NameMap, _ = _models(prod)
        rows = session.exec(select(Customer).where(Customer.is_closed == False)).all()  # noqa: E712
        names = {m.customer_id: m for m in session.exec(select(NameMap)).all()}
        aliases: dict[int, list[str]] = {}
        for a in session.exec(
            select(CustomerAlias).where(CustomerAlias.finance_type == prod.upper(), CustomerAlias.active_customer_id != None)  # noqa: E711
        ).all():
            aliases.setdefault(a.active_customer_id, []).append(a.alias_name)
        for c in rows:
            key = f"{prod}:{c.customer_id}"
            nm = names.get(c.customer_id)
            en = (nm.customer_name_en if nm and nm.customer_name_en else c.customer_name) or ""
            ta = (nm.customer_name_ta if nm and nm.customer_name_ta else c.customer_name_ta) or ""
            customers[key] = {
                "product": prod, "customer_id": c.customer_id, "name_en": en, "name_ta": ta,
                "outstanding": _num(c.outstanding_balance), "phone": re.sub(r"\D", "", c.contact_number or ""),
            }
            if not phone_query:
                for variant in [en, ta, *aliases.get(c.customer_id, [])]:
                    if _norm(variant):
                        candidates.append({"customer_id": key, "name": _norm(variant), "display_name": variant})

    best: dict[str, float] = {}
    if phone_query:
        if len(digits_only) >= 3:
            for key, info in customers.items():
                if digits_only in info["phone"]:
                    best[key] = 100.0
    else:
        for hit in get_similar_score(text, candidates):
            if hit["score"] >= MIN_SCORE and hit["score"] > best.get(hit["customer_id"], 0.0):
                best[hit["customer_id"]] = hit["score"]

    ranked = sorted(best.items(), key=lambda kv: (-kv[1], customers[kv[0]]["name_en"].lower()))[:MAX_RESULTS]
    return [
        {k: customers[key][k] for k in ("product", "customer_id", "name_en", "name_ta", "outstanding")} | {"score": score}
        for key, score in ranked
    ]


def customer_detail(session: Session, product: str, customer_id: int) -> Optional[dict]:
    """Everything the TV shows for one ACTIVE customer. Aadhaar, remarks and segment are deliberately not included."""
    if product not in PRODUCTS:
        return None
    Customer, NameMap, Txn = _models(product)
    c = session.get(Customer, customer_id)
    if not c or c.is_closed:
        return None
    nm = session.get(NameMap, customer_id)
    name_en = (nm.customer_name_en if nm and nm.customer_name_en else c.customer_name) or ""
    name_ta = (nm.customer_name_ta if nm and nm.customer_name_ta else c.customer_name_ta) or ""

    txns = session.exec(
        select(Txn).where(Txn.customer_id == customer_id).order_by(Txn.collection_date.desc(), Txn.transaction_id.desc())
    ).all()
    payments, paid_sum = [], Decimal(0)
    for t in txns:
        principal = Decimal(str(getattr(t, "principal_amount", 0) or 0))
        is_paid = (t.payment_status or "").upper() == "PAID"
        if is_paid:
            paid_sum += Decimal(str(t.amount or 0))
        payments.append({
            "date": t.collection_date.isoformat(),
            "amount": float(Decimal(str(t.amount or 0)) + principal),      # what was handed over (IOP: interest + principal)
            "principal": float(principal),
            "gg": (t.payment_mode or "").upper() == "ONLINE",              # "GG" = marked as Google Pay
            "paid": is_paid,                                               # a GG mark with paid=false is the red X: the money did not arrive
        })
    if product == "iop":
        paid_sum += Decimal(str(c.principal_paid or 0))                    # principal already returned counts as paid

    out = {
        "product": product, "customer_id": c.customer_id, "name_en": name_en, "name_ta": name_ta,
        "phone": c.contact_number or "", "address": c.customer_address or "",
        "loan_start_date": c.loan_start_date.isoformat() if c.loan_start_date else None,
        "loan_amount": _num(c.loan_amount), "interest": _num(c.interest), "outstanding": _num(c.outstanding_balance),
        "total_paid": float(paid_sum), "payments": payments,
    }
    if product == "edi":
        out["disbursed_amount"] = _num(c.disbursed_amount)
    else:
        out["principal_paid"] = _num(c.principal_paid)
    return out


# ── The collections page (வசூல்) ─────────────────────────────────────────────────────────────────────────────
# It mirrors two dashboard reports, so the TV and the website always agree:
#   EL  = "Reminder report - IOP Interest Due": who is due Yesterday / Today / Tomorrow (the same payment-date rule,
#         dashboard_service._payment_dates_in_range), the loan, and the interest of ONE collection (the customer's `interest`).
#   தவணை = "Defaulter report - EDI 95+ days overdue": days since the last PAID payment (the loan start date when nothing was ever paid).

IST = timezone(timedelta(hours=5, minutes=30))
EDI_OVERDUE_DAYS = 95                   # the Defaulter report's own threshold (DashboardService.get_edi_defaulters)
MONTHLY_FROM = 30                       # interest collected every 30 days (or more) = "maadha vatti"; shorter (10 days ...) goes in the upper card
DAY_KEYS = (("yesterday", -1), ("today", 0), ("tomorrow", 1))


def today_ist() -> date:
    return datetime.now(IST).date()


def _name_pair(c, nm) -> tuple[str, str]:
    en = (nm.customer_name_en if nm and nm.customer_name_en else c.customer_name) or ""
    ta = (nm.customer_name_ta if nm and nm.customer_name_ta else c.customer_name_ta) or ""
    return en, ta


def collections(session: Session, today: Optional[date] = None) -> dict:
    """What the TV's collections page shows (read-only; active customers; `ignore`d ones left out, as both reports do by default).

    el[yesterday | today | tomorrow] = {date, ten_days: [...], monthly: [...]}: the customers due that day, split by how often interest is collected
    (every 30+ days -> monthly, shorter -> ten_days). Per person: loan, `due` = the interest of one collection, `paid_month` = interest PAID so far
    this month (principal excluded), phone.
    edi_long = தவணை loans 95+ days overdue (longest first): last payment date, loan, balance, phone, days overdue. Aadhaar / remarks never leave."""
    from app.services.dashboard_service import _payment_dates_in_range      # the very rule the Reminder report uses

    today = today or today_ist()
    month_start = today.replace(day=1)

    el_names = {m.customer_id: m for m in session.exec(select(IopNameMap)).all()}
    paid: dict[int, Decimal] = {}
    for t in session.exec(
        select(IopTransaction).where(IopTransaction.collection_date >= month_start, IopTransaction.collection_date <= today)
    ).all():
        if (t.payment_status or "").upper() == "PAID":
            paid[t.customer_id] = paid.get(t.customer_id, Decimal(0)) + Decimal(str(t.amount or 0))

    el: dict[str, dict] = {key: {"date": (today + timedelta(days=off)).isoformat(), "ten_days": [], "monthly": []} for key, off in DAY_KEYS}
    for c in session.exec(
        select(IopCustomer).where(IopCustomer.is_closed == False, IopCustomer.ignore == False).order_by(IopCustomer.customer_id)  # noqa: E712
    ).all():
        if not c.loan_start_date:
            continue
        freq = float(c.interest_payment_frequency or 1)                     # a missing frequency counts as 1, like the report
        en, ta = _name_pair(c, el_names.get(c.customer_id))
        row = {
            "customer_id": c.customer_id, "name_en": en, "name_ta": ta,
            "loan_amount": _num(c.loan_amount), "due": _num(c.interest), "paid_month": float(paid.get(c.customer_id, Decimal(0))),
            "phone": c.contact_number or "", "frequency": round(freq),
        }
        for key, off in DAY_KEYS:
            d = today + timedelta(days=off)
            if _payment_dates_in_range(c.loan_start_date, freq, d, d):
                el[key]["monthly" if round(freq) >= MONTHLY_FROM else "ten_days"].append(row)

    edi_names = {m.customer_id: m for m in session.exec(select(EdiNameMap)).all()}
    last_paid: dict[int, date] = {}
    for cid, last in session.exec(
        select(EdiTransaction.customer_id, func.max(EdiTransaction.collection_date)).where(EdiTransaction.payment_status == "PAID").group_by(EdiTransaction.customer_id)
    ).all():
        last_paid[cid] = last
    long_loans: list[dict] = []
    for c in session.exec(select(EdiCustomer).where(EdiCustomer.is_closed == False, EdiCustomer.ignore == False)).all():  # noqa: E712
        since = last_paid.get(c.customer_id) or c.loan_start_date or today      # the report: last paid, else the start date
        days = (today - since).days
        if days < EDI_OVERDUE_DAYS:
            continue
        en, ta = _name_pair(c, edi_names.get(c.customer_id))
        lp = last_paid.get(c.customer_id)
        long_loans.append({
            "customer_id": c.customer_id, "name_en": en, "name_ta": ta,
            "last_payment_date": lp.isoformat() if lp else None, "loan_start_date": c.loan_start_date.isoformat() if c.loan_start_date else None,
            "loan_amount": _num(c.loan_amount), "balance": _num(c.outstanding_balance), "phone": c.contact_number or "", "days": days,
        })
    long_loans.sort(key=lambda r: (-r["days"], r["customer_id"]))

    return {"as_of": today.isoformat(), "el": el, "edi_long": long_loans}
