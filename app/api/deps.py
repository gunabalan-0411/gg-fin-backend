from fastapi import Depends, HTTPException, Request, status
from jose import JWTError
from sqlmodel import Session

from app.core.database import get_session
from app.core.security import decode_token
from app.models.user import User
from app.services.auth_service import AuthService


def get_current_user(
    request: Request,
    session: Session = Depends(get_session),
) -> User:
    """
    Accept the JWT from either:
      1. httpOnly cookie `gg_fin_token`  (browser requests)
      2. Authorization: Bearer <token>   (dev tools / API clients)
    """
    token = request.cookies.get("gg_fin_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_token(token)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    user = AuthService(session).get_current_user(username)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive or unknown user")
    return user
