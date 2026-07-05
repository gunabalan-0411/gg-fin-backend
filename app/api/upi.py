from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlmodel import Session, select, col
from typing import Optional

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.models.upi import UpiTransaction, UpiVpaMapping
from app.services import upi_service

router = APIRouter()


def _callback_base(request: Request) -> str:
    """Return the backend base URL, respecting Railway's HTTPS proxy headers."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


# ── Gmail OAuth ───────────────────────────────────────────────────────────────

@router.get("/gmail/auth-url")
def gmail_auth_url(request: Request, _=Depends(get_current_user)):
    """Return the Google OAuth URL for the frontend to open."""
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=400,
            detail="GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not configured in .env",
        )
    redirect_uri = (
        settings.GMAIL_REDIRECT_URI
        or f"{_callback_base(request)}/api/upi/gmail/callback"
    )
    # Pass the frontend origin through OAuth state so the callback can redirect
    # back to the correct host regardless of FRONTEND_URL env var.
    frontend_origin = (
        settings.FRONTEND_URL
        or request.headers.get("origin")
        or "http://localhost:3000"
    )
    try:
        url = upi_service.get_auth_url(redirect_uri, frontend_origin)
        return {"url": url}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/gmail/callback")
def gmail_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(default=""),
    session: Session = Depends(get_session),
):
    """Google redirects here after user consent. No auth required (public)."""
    redirect_uri = (
        settings.GMAIL_REDIRECT_URI
        or f"{_callback_base(request)}/api/upi/gmail/callback"
    )
    # state carries the frontend origin set in gmail_auth_url
    frontend_origin = state if state and state != "default" else settings.FRONTEND_URL or "http://localhost:3000"
    try:
        upi_service.exchange_code(code, redirect_uri, session)
    except Exception as e:
        return RedirectResponse(
            url=f"{frontend_origin}/oauth-callback?type=gmail&status=error&msg={str(e)[:80]}"
        )
    return RedirectResponse(url=f"{frontend_origin}/oauth-callback?type=gmail&status=connected")


@router.get("/gmail/status")
def gmail_status(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    return upi_service.get_gmail_status(session)


@router.delete("/gmail/disconnect")
def gmail_disconnect(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    upi_service.disconnect_gmail(session)
    return {"ok": True}


@router.get("/gmail/debug-emails")
def gmail_debug_emails(
    limit: int = Query(default=20, ge=1, le=50),
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """
    Fetch last `limit` HDFC emails (broad query, last 90 days) and return
    raw body previews + regex match status. Use this to diagnose missing formats.
    """
    try:
        return upi_service.debug_gmail_emails(session, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gmail/sync")
def gmail_sync(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """Fetch last-year HDFC credit emails and import UPI transactions."""
    try:
        result = upi_service.sync_gmail(session)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import logging, traceback
        logging.getLogger(__name__).error("Gmail sync failed: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/gmail/sync-stream")
def gmail_sync_stream(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """SSE endpoint — streams per-email progress while syncing Gmail."""
    return StreamingResponse(
        upi_service.sync_gmail_stream(session),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── CSV import ────────────────────────────────────────────────────────────────

@router.post("/import-csv")
async def import_csv(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    content = await file.read()
    try:
        result = upi_service.import_csv(content, session)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Transactions CRUD ─────────────────────────────────────────────────────────

@router.get("/transactions")
def list_transactions(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    source: Optional[str] = None,
    mapped: Optional[bool] = None,
    skip: int = 0,
    limit: int = 100,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    q = select(UpiTransaction)
    if date_from:
        q = q.where(UpiTransaction.transaction_date >= date_from)
    if date_to:
        q = q.where(UpiTransaction.transaction_date <= date_to)
    if source:
        q = q.where(UpiTransaction.source == source)
    if mapped is True:
        q = q.where(UpiTransaction.mapped_customer_id.isnot(None))
    elif mapped is False:
        q = q.where(UpiTransaction.mapped_customer_id.is_(None))

    from app.models.customer import EdiCustomer, IopCustomer
    total = len(session.exec(q).all())
    rows = session.exec(q.order_by(UpiTransaction.transaction_date.desc()).offset(skip).limit(limit)).all()

    # Bulk-lookup customer names
    edi_ids = {r.mapped_customer_id for r in rows if r.mapped_customer_type == "edi" and r.mapped_customer_id}
    iop_ids = {r.mapped_customer_id for r in rows if r.mapped_customer_type == "iop" and r.mapped_customer_id}
    edi_names = {c.customer_id: c.customer_name for c in session.exec(select(EdiCustomer).where(col(EdiCustomer.customer_id).in_(list(edi_ids)))).all()} if edi_ids else {}
    iop_names = {c.customer_id: c.customer_name for c in session.exec(select(IopCustomer).where(col(IopCustomer.customer_id).in_(list(iop_ids)))).all()} if iop_ids else {}

    data = []
    for r in rows:
        d = r.model_dump()
        if r.mapped_customer_type == "edi":
            d["mapped_customer_name"] = edi_names.get(r.mapped_customer_id)
        elif r.mapped_customer_type == "iop":
            d["mapped_customer_name"] = iop_names.get(r.mapped_customer_id)
        else:
            d["mapped_customer_name"] = None
        data.append(d)
    return {"data": data, "total": total}


@router.patch("/transactions/{txn_id}/map")
def map_customer(
    txn_id: int,
    customer_id: Optional[int] = None,
    customer_type: Optional[str] = None,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    txn = session.get(UpiTransaction, txn_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    txn.mapped_customer_id = customer_id
    txn.mapped_customer_type = customer_type
    session.add(txn)
    session.commit()
    session.refresh(txn)
    return txn


@router.delete("/transactions/{txn_id}", status_code=204)
def delete_transaction(
    txn_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    txn = session.get(UpiTransaction, txn_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    session.delete(txn)
    session.commit()


# ── UPI helpers for mapping UX ────────────────────────────────────────────────

@router.get("/unique-vpas")
def unique_vpas(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """Unique sender VPAs from last 6 months sorted by latest transaction date desc."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).date()
    rows = session.exec(
        select(UpiTransaction.sender_vpa, UpiTransaction.sender_name, UpiTransaction.transaction_date)
        .where(UpiTransaction.sender_vpa.is_not(None))
        .where(UpiTransaction.sender_vpa != "")
        .where(UpiTransaction.transaction_date >= cutoff)
    ).all()
    vpa_map: dict = {}
    for vpa, name, txn_date in rows:
        if vpa not in vpa_map:
            vpa_map[vpa] = {"vpa": vpa, "sender_name": name or "", "count": 0, "latest_date": txn_date}
        vpa_map[vpa]["count"] += 1
        if txn_date > vpa_map[vpa]["latest_date"]:
            vpa_map[vpa]["latest_date"] = txn_date
    data = sorted(vpa_map.values(), key=lambda x: x["latest_date"], reverse=True)
    for item in data:
        item["latest_date"] = item["latest_date"].isoformat()
    return {"data": data}


@router.get("/customers-with-balance")
def customers_with_balance(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """EDI and IOP customers with outstanding_balance > 0."""
    from app.models.customer import EdiCustomer, IopCustomer
    from sqlmodel import col
    edi = session.exec(select(EdiCustomer).where(col(EdiCustomer.outstanding_balance) > 0)).all()
    iop = session.exec(select(IopCustomer).where(col(IopCustomer.outstanding_balance) > 0)).all()
    result = []
    for c in edi:
        result.append({
            "customer_id": c.customer_id,
            "customer_name": c.customer_name or "",
            "type": "edi",
            "balance": float(c.outstanding_balance or 0),
        })
    for c in iop:
        result.append({
            "customer_id": c.customer_id,
            "customer_name": c.customer_name or "",
            "type": "iop",
            "balance": float(c.outstanding_balance or 0),
        })
    result.sort(key=lambda x: x["customer_name"])
    return {"data": result}


@router.post("/fuzzy-suggest")
def fuzzy_suggest(
    query: str,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """Fuzzy match a name against customers with outstanding balance > 0."""
    from app.models.customer import EdiCustomer, IopCustomer
    from sqlmodel import col
    from app.utils.name_matching import get_similar_score
    edi = session.exec(select(EdiCustomer).where(col(EdiCustomer.outstanding_balance) > 0)).all()
    iop = session.exec(select(IopCustomer).where(col(IopCustomer.outstanding_balance) > 0)).all()
    candidates = []
    meta = {}
    for c in edi:
        name = c.customer_name or ""
        if not name:
            continue
        key = f"edi_{c.customer_id}"
        candidates.append({"customer_id": key, "name": name.lower().strip(), "display_name": name})
        meta[key] = {"type": "edi", "id": c.customer_id, "balance": float(c.outstanding_balance or 0)}
    for c in iop:
        name = c.customer_name or ""
        if not name:
            continue
        key = f"iop_{c.customer_id}"
        candidates.append({"customer_id": key, "name": name.lower().strip(), "display_name": name})
        meta[key] = {"type": "iop", "id": c.customer_id, "balance": float(c.outstanding_balance or 0)}
    scored = get_similar_score(query, candidates)
    result = []
    for s in scored[:8]:
        info = meta.get(s["customer_id"], {})
        result.append({
            "customer_id": info.get("id"),
            "customer_name": s["name"],
            "type": info.get("type", "edi"),
            "score": s["score"],
            "balance": info.get("balance", 0),
        })
    return {"data": result}


# ── VPA Mappings ───────────────────────────────────────────────────────────────

@router.get("/vpa-mappings")
def list_vpa_mappings(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    rows = session.exec(select(UpiVpaMapping)).all()
    return {"data": rows}


@router.post("/vpa-mappings", status_code=201)
def create_vpa_mapping(
    upi_vpa: str,
    customer_id: int,
    customer_type: str,
    customer_name: Optional[str] = None,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    vpa_lower = upi_vpa.lower()
    existing = session.exec(
        select(UpiVpaMapping)
        .where(col(UpiVpaMapping.upi_vpa) == vpa_lower)
        .where(UpiVpaMapping.customer_type == customer_type)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"VPA already mapped to an {customer_type.upper()} customer")
    m = UpiVpaMapping(
        upi_vpa=vpa_lower,
        customer_id=customer_id,
        customer_type=customer_type,
        customer_name=customer_name,
    )
    session.add(m)
    session.flush()

    # Auto-apply: update all transactions with this VPA (case-insensitive)
    from sqlalchemy import func
    matching_txns = session.exec(
        select(UpiTransaction).where(func.lower(UpiTransaction.sender_vpa) == vpa_lower)
    ).all()
    for txn in matching_txns:
        txn.mapped_customer_id = customer_id
        txn.mapped_customer_type = customer_type
        session.add(txn)

    session.commit()
    session.refresh(m)
    return m


@router.delete("/vpa-mappings/{mapping_id}", status_code=204)
def delete_vpa_mapping(
    mapping_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    m = session.get(UpiVpaMapping, mapping_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mapping not found")
    session.delete(m)
    session.commit()
