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
from app.services import ocr_service
from app.services.transaction_service import EdiTransactionService, IopTransactionService

router = APIRouter()


@router.post("/upload")
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    _=Depends(get_current_user),
):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")
    content = await file.read()
    try:
        # save_and_warm preprocesses page 0 before returning so the browser
        # can display it the instant the upload response arrives.
        session_id, total_pages = await run_in_threadpool(ocr_service.save_and_warm, content)
    except Exception as exc:
        raise HTTPException(500, f"Failed to process PDF: {exc}")

    # Preprocess the remaining pages in the background so navigation is instant.
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
    # EDI: only customers with outstanding_balance > 0, using English name map
    edi_active = {
        c.customer_id
        for c in session.exec(
            select(EdiCustomer).where(col(EdiCustomer.outstanding_balance) > 0)
        ).all()
    }
    edi_list = [
        {"id": r.customer_id, "name": r.customer_name_en}
        for r in session.exec(select(EdiNameMap)).all()
        if r.customer_id in edi_active and r.customer_name_en
    ]

    # IOP: only customers with loan_closure > 0, using English name map
    iop_active = {
        c.customer_id
        for c in session.exec(
            select(IopCustomer).where(col(IopCustomer.loan_closure) > 0)
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

    for rec in records:
        product = (rec.get("product_type") or "EDI").upper()
        pool = iop_list if product == "IOP" else edi_list
        matches = ocr_service.fuzzy_match(rec.get("customer_name", ""), pool)
        rec["customer_suggestions"] = matches
        best = matches[0] if matches else None
        rec["customer_id"] = best["id"] if best and best["score"] >= 0.90 else None

    return {"page_image_b64": page_b64, "records": records}


class OcrRecord(BaseModel):
    collection_date: str
    customer_name: str
    customer_id: Optional[int]
    product_type: str
    payment_mode: str
    is_paid: bool = True
    amount: int


class SubmitRequest(BaseModel):
    records: list[OcrRecord]


@router.post("/submit")
def submit_records(
    body: SubmitRequest,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    saved = 0
    for rec in body.records:
        if not rec.customer_id or not rec.amount:
            continue
        try:
            col_date = datetime.strptime(rec.collection_date, "%d-%m-%Y").date()
        except ValueError:
            continue
        svc = (
            IopTransactionService(session)
            if rec.product_type.upper() == "IOP"
            else EdiTransactionService(session)
        )
        svc.upsert(rec.customer_id, col_date, rec.amount, rec.payment_mode, rec.is_paid)
        saved += 1
    return {"submitted": saved}
