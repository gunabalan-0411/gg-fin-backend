"""Note Pad endpoints (Markdown pages). All rules live in app/services/note_service.py."""
from typing import Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session
from app.schemas.note import NotePageCreate, NotePageRead, NotePageSummary, NotePageUpdate
from app.services import note_service as svc
from app.services.note_service import NoteError

router = APIRouter()
T = TypeVar("T")


def _run(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except NoteError as e:
        raise HTTPException(status_code=e.status, detail=e.payload())


@router.get("", response_model=list[NotePageSummary])
def list_pages(session: Session = Depends(get_session), _=Depends(get_current_user)):
    return svc.list_pages(session)


@router.post("", response_model=NotePageRead, status_code=201)
def create_page(body: NotePageCreate, session: Session = Depends(get_session), _=Depends(get_current_user)):
    return _run(lambda: svc.create_page(session, body.title, body.content))


@router.get("/{page_id}", response_model=NotePageRead)
def get_page(page_id: int, session: Session = Depends(get_session), _=Depends(get_current_user)):
    return _run(lambda: svc.get_page(session, page_id))


@router.patch("/{page_id}", response_model=NotePageRead)
def update_page(page_id: int, body: NotePageUpdate, session: Session = Depends(get_session), _=Depends(get_current_user)):
    """Rename and / or save the text. 409 `stale` (with the current page) when `version` is not the latest."""
    return _run(lambda: svc.update_page(session, page_id, body.version, body.title, body.content))


@router.delete("/{page_id}", status_code=204)
def delete_page(page_id: int, session: Session = Depends(get_session), _=Depends(get_current_user)):
    _run(lambda: svc.delete_page(session, page_id))
