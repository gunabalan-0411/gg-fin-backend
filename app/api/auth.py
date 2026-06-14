import threading
from collections import defaultdict
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.services.auth_service import AuthService

router = APIRouter()

# ── Simple in-memory rate limiter (no extra dependency) ───────────────────────
_login_attempts: dict[str, list[float]] = defaultdict(list)
_ratelimit_lock = threading.Lock()
_RATE_WINDOW_S  = 60   # seconds
_RATE_MAX       = 10   # attempts per window per IP

def _is_rate_limited(ip: str) -> bool:
    now = monotonic()
    with _ratelimit_lock:
        clean = [t for t in _login_attempts[ip] if now - t < _RATE_WINDOW_S]
        _login_attempts[ip] = clean
        if len(clean) >= _RATE_MAX:
            return True
        _login_attempts[ip].append(now)
        return False

# ── Cookie helpers ─────────────────────────────────────────────────────────────
_COOKIE_NAME = "gg_fin_token"
_COOKIE_MAX_AGE = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,        # HTTPS only (Railway enforces HTTPS)
        samesite="strict",
        max_age=_COOKIE_MAX_AGE,
        path="/",
    )

def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key=_COOKIE_NAME, path="/", samesite="strict")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/login")
def login(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):
    client_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
    if _is_rate_limited(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please wait a minute.",
        )
    token = AuthService(session).authenticate(form_data.username, form_data.password)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    _set_auth_cookie(response, token)
    # Also return token in body so existing Bearer-header clients keep working during transition
    return {"access_token": token, "token_type": "bearer"}


@router.post("/logout")
def logout(response: Response):
    _clear_auth_cookie(response)
    return {"ok": True}


@router.post("/refresh")
def refresh_token(response: Response, current_user: User = Depends(get_current_user)):
    token = create_access_token(current_user.username)
    _set_auth_cookie(response, token)
    return {"access_token": token, "token_type": "bearer"}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    current_user.hashed_password = hash_password(body.new_password)
    session.add(current_user)
    session.commit()
    return {"ok": True}
