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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

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
