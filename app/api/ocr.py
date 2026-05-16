from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.customer import EdiCustomer, IopCustomer
from app.services import ocr_service
from app.services.transaction_service import EdiTransactionService, IopTransactionService

router = APIRouter()


@router.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    _=Depends(get_current_user),
):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")
    content = await file.read()
    try:
        session_id, total_pages = await run_in_threadpool(ocr_service.save_pdf, content)
    except Exception as exc:
        raise HTTPException(500, f"Failed to process PDF: {exc}")
    return {"session_id": session_id, "total_pages": total_pages}


class ExtractRequest(BaseModel):
    session_id: str
    page_index: int


@router.post("/extract")
async def extract_page(
    body: ExtractRequest,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    edi_rows = session.exec(select(EdiCustomer)).all()
    iop_rows = session.exec(select(IopCustomer)).all()
    edi_list = [{"id": c.customer_id, "name": c.customer_name} for c in edi_rows if c.customer_name]
    iop_list = [{"id": c.customer_id, "name": c.customer_name} for c in iop_rows if c.customer_name]

    try:
        page_b64, records = await run_in_threadpool(
            ocr_service.extract_page, body.session_id, body.page_index
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
        rec["customer_id"] = best["id"] if best and best["score"] >= 0.75 else None

    return {"page_image_b64": page_b64, "records": records}


class OcrRecord(BaseModel):
    collection_date: str
    customer_name: str
    customer_id: Optional[int]
    product_type: str
    payment_mode: str
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
        svc.upsert(rec.customer_id, col_date, rec.amount, rec.payment_mode)
        saved += 1
    return {"submitted": saved}
