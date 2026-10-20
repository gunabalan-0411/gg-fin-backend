"""
Note Pad: a handful of Markdown pages kept in the database (so they survive deploys, are the same on every device and are
part of the Drive backup). Saves carry the version they were based on; a stale save is refused instead of overwriting.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.note import NotePage

MAX_PAGES = 20
MAX_CONTENT_BYTES = 200_000     # per page (UTF-8)
MAX_TITLE = 100
DEFAULT_TITLE = "Untitled"


class NoteError(Exception):
    """Business-rule failure; the API turns it into an HTTP error with a machine-readable `code`."""

    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def payload(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clean_title(title: Optional[str]) -> str:
    t = " ".join((title or "").split())
    if len(t) > MAX_TITLE:
        raise NoteError(422, "title_too_long", f"A page title can be at most {MAX_TITLE} characters")
    return t or DEFAULT_TITLE


def check_size(content: str) -> None:
    size = len(content.encode("utf-8"))
    if size > MAX_CONTENT_BYTES:
        raise NoteError(422, "too_large", f"A page can hold at most {MAX_CONTENT_BYTES // 1000} KB of text "
                                          f"(this one is {size // 1000} KB)", limit=MAX_CONTENT_BYTES, size=size)


def summary(p: NotePage) -> dict:
    return {"id": p.id, "title": p.title, "version": p.version, "size": len(p.content.encode("utf-8")), "updated_at": p.updated_at}


def list_pages(session: Session) -> list[dict]:
    return [summary(p) for p in session.exec(select(NotePage).order_by(NotePage.id)).all()]


def get_page(session: Session, page_id: int) -> NotePage:
    p = session.get(NotePage, page_id)
    if p is None:
        raise NoteError(404, "not_found", "That page no longer exists")
    return p


def create_page(session: Session, title: Optional[str], content: str) -> NotePage:
    if (session.exec(select(func.count()).select_from(NotePage)).one() or 0) >= MAX_PAGES:
        raise NoteError(409, "limit_reached", f"At most {MAX_PAGES} pages — delete one to make room", limit=MAX_PAGES)
    check_size(content)
    now = _now()
    p = NotePage(title=clean_title(title), content=content, version=1, created_at=now, updated_at=now)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def update_page(session: Session, page_id: int, version: int, title: Optional[str], content: Optional[str]) -> NotePage:
    p = get_page(session, page_id)
    if p.version != version:
        raise NoteError(409, "stale", "This page was changed somewhere else since you opened it",
                        current={"id": p.id, "title": p.title, "content": p.content, "version": p.version,
                                 "created_at": p.created_at.isoformat(), "updated_at": p.updated_at.isoformat()})
    new_title = clean_title(title) if title is not None else p.title
    if content is not None:
        check_size(content)
    if new_title == p.title and (content is None or content == p.content):
        return p                                       # nothing changed: no new version
    p.title = new_title
    if content is not None:
        p.content = content
    p.version += 1
    p.updated_at = _now()
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def delete_page(session: Session, page_id: int) -> None:
    session.delete(get_page(session, page_id))
    session.commit()
