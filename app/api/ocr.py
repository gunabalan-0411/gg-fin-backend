import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.customer import EdiCustomer, IopCustomer
from app.models.mapping import EdiNameMap, IopNameMap
from app.schemas.name_mapping import MappingRef
from app.services import name_mapping_service, ocr_service
from app.services.mapping_matcher import HIGH_CONFIDENCE
from app.services.transaction_service import EdiTransactionService, IopTransactionService

router = APIRouter()
log = logging.getLogger(__name__)


_ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


@router.post("/upload")
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    _=Depends(get_current_user),
):
    fname = (file.filename or "").lower()
    ext = "." + fname.rsplit(".", 1)[-1] if "." in fname else ""
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Only PDF, JPG, or PNG files are accepted")
    content = await file.read()
    try:
        if ext == ".pdf":
            session_id, total_pages = await run_in_threadpool(ocr_service.save_and_warm, content)
        else:
            session_id, total_pages = await run_in_threadpool(
                ocr_service.save_image_and_warm, content, fname
            )
    except Exception as exc:
        raise HTTPException(500, f"Failed to process file: {exc}")

    # Preprocess remaining PDF pages in the background
    if total_pages > 1:
        background_tasks.add_task(
            ocr_service.preprocess_remaining_pages, session_id, total_pages
        )
    return {"session_id": session_id, "total_pages": total_pages}


@router.get("/page/{session_id}/{page_index}")
async def get_page_image(
    session_id: str,
    page_index: int,
    _=Depends(get_current_user),
):
    try:
        page_b64 = await run_in_threadpool(ocr_service.get_page_image_b64, session_id, page_index)
    except FileNotFoundError:
        raise HTTPException(404, "OCR session not found or expired — please re-upload the PDF")
    except Exception as exc:
        raise HTTPException(500, f"Failed to render page: {exc}")
    return {"page_image_b64": page_b64}


@router.get("/page/{session_id}/{page_index}/ink")
async def get_page_ink(
    session_id: str,
    page_index: int,
    _=Depends(get_current_user),
):
    """The page's ink for the scan animation: `{width, height, mask_png_b64, blobs: [[x, y, w, h], ...], coverage}` (OpenCV)."""
    try:
        return await run_in_threadpool(ocr_service.get_page_ink, session_id, page_index)
    except FileNotFoundError:
        raise HTTPException(404, "OCR session not found or expired — please re-upload the PDF")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Failed to analyse the page: {exc}")


class ExtractRequest(BaseModel):
    session_id: str
    page_index: int
    model: str = "gemini-2.5-flash"


@router.post("/extract")
async def extract_page(
    body: ExtractRequest,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    # EDI: only open accounts, using English name map
    edi_active = {
        c.customer_id
        for c in session.exec(
            select(EdiCustomer).where(EdiCustomer.is_closed == False)  # noqa: E712
        ).all()
    }
    edi_list = [
        {"id": r.customer_id, "name": r.customer_name_en}
        for r in session.exec(select(EdiNameMap)).all()
        if r.customer_id in edi_active and r.customer_name_en
    ]

    # IOP: only open accounts, using English name map
    iop_active = {
        c.customer_id
        for c in session.exec(
            select(IopCustomer).where(IopCustomer.is_closed == False)  # noqa: E712
        ).all()
    }
    iop_list = [
        {"id": r.customer_id, "name": r.customer_name_en}
        for r in session.exec(select(IopNameMap)).all()
        if r.customer_id in iop_active and r.customer_name_en
    ]

    try:
        page_b64, records = await run_in_threadpool(
            ocr_service.extract_page, body.session_id, body.page_index, body.model
        )
    except FileNotFoundError:
        raise HTTPException(404, "OCR session not found or expired — please re-upload the PDF")
    except ValueError as exc:
        raise HTTPException(503, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Extraction failed: {exc}")

    # Alias / AI-mismatch mappings are loaded once for the whole page. If they cannot be read (e.g. migration not
    # applied) OCR keeps working with the plain fuzzy match and the response carries a visible warning.
    mapping_index = None
    mapping_warning = None
    try:
        mapping_index = name_mapping_service.MappingIndex(session)
    except Exception as exc:
        session.rollback()
        log.exception("Name-mapping lookup unavailable during OCR extract")
        mapping_warning = f"Alias / AI-mismatch matching is unavailable: {exc.__class__.__name__}"

    for rec in records:
        product = (rec.get("product_type") or "EDI").upper()
        ftype = "IOP" if product == "IOP" else "EDI"
        pool = iop_list if product == "IOP" else edi_list
        name = rec.get("customer_name", "")

        # Step 1 — existing fuzzy match on active customer names (unchanged); column 1 of the dropdown
        matches = ocr_service.fuzzy_match(name, pool)
        rec["customer_suggestions"] = matches
        best = matches[0] if matches else None

        # Column 2 — alias / AI-mismatch candidates, always computed so the user can open the dropdown and choose
        mapped, auto = ([], None)
        if mapping_index is not None:
            try:
                mapped, auto = mapping_index.suggest(ftype, name)
            except Exception:
                session.rollback()
                log.exception("Name-mapping lookup failed for %r", name)
        rec["mapping_suggestions"] = [c.to_dict() for c in mapped]
        rec["match_source"] = None
        rec["mapping_ref"] = None
        rec["matched_customer_name"] = None

        if best and best["score"] >= HIGH_CONFIDENCE:
            # High confidence: stop here, no mapping lookup — the user decides from the dropdown (R-02)
            rec["customer_id"] = best["id"]
            rec["match_source"] = "fuzzy"
        else:
            rec["customer_id"] = None
            if auto is not None:
                # Steps 2 & 3 — AI-mismatch first, then 2nd alias (both at the same 0.90 threshold)
                rec["customer_id"] = auto.customer_id
                rec["match_source"] = auto.source
                rec["mapping_ref"] = {"kind": auto.source, "id": auto.mapping_id}
                rec["matched_customer_name"] = auto.customer_name

    resp = {"page_image_b64": page_b64, "records": records}
    if mapping_warning:
        resp["mapping_warning"] = mapping_warning
    return resp


class OcrRecord(BaseModel):
    collection_date: str
    customer_name: str
    customer_id: Optional[int]
    product_type: str
    payment_mode: str
    is_paid: bool = True
    amount: int
    mapping_ref: Optional[MappingRef] = None   # set when the customer was picked through an alias / AI-mismatch entry


class SubmitRequest(BaseModel):
    records: list[OcrRecord]


@router.post("/submit")
def submit_records(
    body: SubmitRequest,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    saved = 0
    usage_refs: list[dict] = []
    for rec in body.records:
        if not rec.customer_id or not rec.amount:
            continue
        try:
            col_date = datetime.strptime(rec.collection_date, "%d-%m-%Y").date()
        except ValueError:
            continue
        is_iop = rec.product_type.upper() == "IOP"
        svc = IopTransactionService(session) if is_iop else EdiTransactionService(session)
        svc.upsert(rec.customer_id, col_date, rec.amount, rec.payment_mode, rec.is_paid)
        saved += 1
        if rec.mapping_ref:                       # only rows that were really saved count as a "use" (R-30)
            usage_refs.append({"kind": rec.mapping_ref.kind, "mapping_id": rec.mapping_ref.id,
                               "finance_type": "IOP" if is_iop else "EDI", "customer_id": rec.customer_id})

    usage_counted = 0
    if usage_refs:
        try:
            usage_counted = name_mapping_service.record_usage(session, usage_refs)
        except Exception:                         # the collections are already saved — never fail the submit over a counter
            session.rollback()
            log.exception("Could not record alias / AI-mismatch usage")
    return {"submitted": saved, "mapping_usage": usage_counted}
