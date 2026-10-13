"""
Alias / AI-mismatch name mapping endpoints (OCR page popup, "Remember this", live dropdown column 2, repair).
All business rules live in app/services/name_mapping_service.py.
"""
from typing import Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session
from app.schemas.name_mapping import CheckRequest, RememberRequest, SaveRequest, SuggestRequest
from app.services import name_mapping_service as svc
from app.services.name_mapping_service import MappingError

router = APIRouter()
T = TypeVar("T")


def _run(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except MappingError as e:
        raise HTTPException(status_code=e.status, detail=e.payload())


@router.get("")
def get_customer_mappings(
    finance_type: str,
    customer_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """Popup contents: the customer's alias and AI-mismatch names, usage, stale flags and the limits."""
    return _run(lambda: svc.popup_data(session, finance_type, customer_id))


@router.post("/check")
def check_words(body: CheckRequest, session: Session = Depends(get_session), _=Depends(get_current_user)):
    """Which of these words are already mapped to ANOTHER active customer?"""
    conflicts = _run(lambda: svc.check(session, body.finance_type, body.customer_id, [i.model_dump() for i in body.items]))
    return {"conflicts": conflicts}


@router.put("/customer")
def save_customer_mappings(body: SaveRequest, session: Session = Depends(get_session), _=Depends(get_current_user)):
    """The popup's Save: replace the customer's lists. 422 over the limit, 409 for an unresolved conflict."""
    return _run(lambda: svc.save_customer_mappings(
        session, body.finance_type, body.customer_id, body.aliases, body.mismatches,
        [r.model_dump() for r in body.resolutions],
    ))


@router.post("/remember")
def remember(body: RememberRequest, session: Session = Depends(get_session), _=Depends(get_current_user)):
    """One-click 'Remember this': add the extracted text as an AI-mismatch name (status: added/exists/limit_reached/conflict)."""
    return _run(lambda: svc.remember(session, body.finance_type, body.customer_id, body.word))


@router.post("/suggest")
def suggest(body: SuggestRequest, session: Session = Depends(get_session), _=Depends(get_current_user)):
    """Dropdown column 2 for text the user is typing."""
    def go():
        ftype = svc.normalize_type(body.finance_type)
        index = svc.MappingIndex(session)
        cands, _auto = index.suggest(ftype, body.query)
        return {"candidates": [c.to_dict() for c in cands[: body.limit]]}
    return _run(go)


@router.post("/resync")
def resync(session: Session = Depends(get_session), _=Depends(get_current_user)):
    """Repair: re-point every mapping row at its current customer (the same routine the database triggers use)."""
    return svc.resync(session)
