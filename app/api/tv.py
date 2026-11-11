"""
Home TV (Android TV) API.

    public (the TV, before it is paired):   POST /pair/start, POST /pair/poll        (rate limited)
    logged-in person:                        POST /pair/approve, GET /devices, DELETE /devices/{id}
    the paired TV (header X-TV-Token):       GET /me, GET /search, GET /customers/{edi|iop}/{id}, GET /collections   - read-only

The TV token is not a login: it is not a JWT, so every other route answers 401 to it.
"""
import threading
from collections import defaultdict
from datetime import datetime
from time import monotonic
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.tv import TvDevice
from app.models.user import User
from app.services import tv_service

router = APIRouter()

# ── Small in-memory rate limiter (same idea as the login one) ───────────────────────────────────────────────
_hits: dict[str, list[float]] = defaultdict(list)
_lock = threading.Lock()


def _limited(bucket: str, request: Request, per_minute: int) -> bool:
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown").split(",")[0].strip()
    key = f"{bucket}:{ip}"
    now = monotonic()
    with _lock:
        recent = [t for t in _hits[key] if now - t < 60]
        if len(recent) >= per_minute:
            _hits[key] = recent
            return True
        recent.append(now)
        _hits[key] = recent
        return False


def reset_rate_limits() -> None:
    """For tests."""
    with _lock:
        _hits.clear()


def get_tv_device(
    x_tv_token: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> TvDevice:
    device = tv_service.authenticate_device(session, x_tv_token)
    if not device:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This TV is not paired (or was removed)")
    return device


# ── Public: the TV asks for a code and waits ────────────────────────────────────────────────────────────────

@router.post("/pair/start")
def pair_start(request: Request, session: Session = Depends(get_session)):
    if _limited("start", request, 10):
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait a minute.")
    return tv_service.start_pairing(session)


class PollBody(BaseModel):
    poll_id: str = Field(min_length=8, max_length=200)


@router.post("/pair/poll")
def pair_poll(body: PollBody, request: Request, session: Session = Depends(get_session)):
    if _limited("poll", request, 60):
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait a minute.")
    return tv_service.poll_pairing(session, body.poll_id)


# ── Logged in: approve a TV, list / revoke ──────────────────────────────────────────────────────────────────

class ApproveBody(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: Optional[str] = Field(default=None, max_length=60)


@router.post("/pair/approve")
def pair_approve(
    body: ApproveBody,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if _limited(f"approve{user.id}", request, 20):
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait a minute.")
    pairing = tv_service.approve_pairing(session, user.id, body.code, body.name)
    if not pairing:
        raise HTTPException(status_code=404, detail="That code was not found, has expired, or was already used")
    return {"ok": True, "name": pairing.device_name}


class DeviceOut(BaseModel):
    id: int
    name: str
    created_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None


@router.get("/devices", response_model=list[DeviceOut])
def devices(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    return tv_service.list_devices(session, user.id)


@router.delete("/devices/{device_id}", status_code=204)
def revoke(device_id: int, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    if not tv_service.revoke_device(session, user.id, device_id):
        raise HTTPException(status_code=404, detail="TV not found")


# ── The paired TV: read-only ────────────────────────────────────────────────────────────────────────────────

@router.get("/me")
def me(device: TvDevice = Depends(get_tv_device)):
    return {"name": device.name}


@router.get("/search")
def search(
    q: str = Query(default="", max_length=100),
    product: Optional[str] = Query(default=None, pattern="^(edi|iop)$"),
    session: Session = Depends(get_session),
    _: TvDevice = Depends(get_tv_device),
):
    return {"results": tv_service.search(session, q, product)}


@router.get("/customers/{product}/{customer_id}")
def customer(product: str, customer_id: int, session: Session = Depends(get_session), _: TvDevice = Depends(get_tv_device)):
    detail = tv_service.customer_detail(session, product.lower(), customer_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Customer not found")
    return detail


@router.get("/collections")
def collections(session: Session = Depends(get_session), _: TvDevice = Depends(get_tv_device)):
    return tv_service.collections(session)
