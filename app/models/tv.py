"""
Home TV (Android TV) access.

    tbl_tv_device    a paired TV: the person who approved it, a name, and the HASH of its token (the token itself is shown to the TV once)
    tbl_tv_pairing   a short-lived 6-digit code the TV shows; a logged-in person types it in to approve the TV

A device token only opens the read-only /api/tv/* endpoints (see app/api/tv.py); it is not a login.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, func
from sqlmodel import Field, SQLModel


def _ts() -> Column:
    return Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _ts_null() -> Column:
    return Column(DateTime(timezone=True), nullable=True)


class TvDevice(SQLModel, table=True):
    __tablename__ = "tbl_tv_device"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)                       # the login that approved it (and the only one who can revoke it)
    name: str
    token_hash: str = Field(unique=True, index=True)       # sha256 of the token; the token itself is never stored
    created_at: Optional[datetime] = Field(default=None, sa_column=_ts())
    last_seen_at: Optional[datetime] = Field(default=None, sa_column=_ts_null())
    revoked_at: Optional[datetime] = Field(default=None, sa_column=_ts_null())


class TvPairing(SQLModel, table=True):
    __tablename__ = "tbl_tv_pairing"

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True)                          # 6 digits, shown on the TV
    poll_hash: str = Field(unique=True, index=True)        # sha256 of the secret the TV polls with
    created_at: Optional[datetime] = Field(default=None, sa_column=_ts())
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    approved_user_id: Optional[int] = None
    device_name: Optional[str] = None
    approved_at: Optional[datetime] = Field(default=None, sa_column=_ts_null())
    claimed_at: Optional[datetime] = Field(default=None, sa_column=_ts_null())   # the TV has collected its token (it can only do that once)
