from datetime import date as Date
from types import SimpleNamespace

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session
from app.services.voice_service import (
    VoiceService, get_device_info, set_device as svc_set_device,
    get_model_status, load_model as svc_load_model, unload_model as svc_unload_model,
    reset_model_from_huggingface,
)
from app.services import iop_split_service
from app.services.transaction_service import EdiTransactionService, IopTransactionService

router = APIRouter()


@router.get("/model-status")
def model_status(_=Depends(get_current_user)):
    """Return whether the Whisper model is currently loaded and how long it has been idle."""
    return get_model_status()


@router.post("/model-load")
async def model_load(_=Depends(get_current_user)):
    """Pre-load the Whisper model into RAM. Resets the 60s idle timer."""
    return await run_in_threadpool(svc_load_model)


@router.post("/model-unload")
def model_unload(_=Depends(get_current_user)):
    """Immediately unload the Whisper model and cancel the idle timer."""
    return svc_unload_model()


@router.post("/model-reset")
async def model_reset(_=Depends(get_current_user)):
    """User-confirmed: wipe the cached model (local + Drive) and re-download fresh
    from HuggingFace, then re-upload it to Drive. Use when the Drive-cached model
    is suspected corrupt or won't load."""
    try:
        return await run_in_threadpool(reset_model_from_huggingface)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/device-info")
def device_info(_=Depends(get_current_user)):
    """Return current inference device and whether GPU is available."""
    return get_device_info()


@router.post("/set-device")
def set_device_endpoint(
    payload: dict = Body(...),
    _=Depends(get_current_user),
):
    """Switch between cpu/cuda. Reloads model on next transcription request."""
    device = payload.get("device", "")
    try:
        svc_set_device(device)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return get_device_info()


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    product: str = Form(...),  # "edi" or "iop"
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """Transcribe uploaded audio and return matched entries."""
    if product not in ("edi", "iop"):
        raise HTTPException(status_code=400, detail="product must be 'edi' or 'iop'")

    audio_bytes = await audio.read()
    suffix = "." + (audio.filename or "audio.webm").rsplit(".", 1)[-1]

    svc = VoiceService(session)
    transcription = await run_in_threadpool(svc.transcribe, audio_bytes, suffix, product)
    matched = await run_in_threadpool(svc.match_entries, transcription, product)

    return {"transcription": transcription, "entries": matched}


@router.post("/submit")
def submit_voice_entries(
    entries: list[dict],
    collection_date: Date,
    product: str,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """
    Submit matched entries as transactions.
    Each entry: {customer_id, amount, payment_mode?, principal_amount?}.
    IOP only: `amount` is what was collected and `principal_amount` (optional) is the part of it that is principal; the transaction
    is the interest, the principal is added to the customer's principal_paid (see app/services/iop_split_service.py). A principal above
    the amount or above the remaining balance is refused (422 bad_principal) and NOTHING is saved.
    """
    if product not in ("edi", "iop"):
        raise HTTPException(status_code=400, detail="product must be 'edi' or 'iop'")

    results = []
    split_of: dict[int, dict] = {}
    if product == "edi":
        svc = EdiTransactionService(session)
    else:
        svc = IopTransactionService(session)
        plan = iop_split_service.plan(session, [_split_record(e, collection_date) for e in entries])
        problems = [{"index": p["index"], "message": p["error"]} for p in plan if p["error"]]
        if problems:
            raise HTTPException(status_code=422, detail={"code": "bad_principal", "message": problems[0]["message"], "rows": problems})
        split_of = {p["index"]: p for p in plan}

    principal_added = 0.0
    for idx, entry in enumerate(entries):
        customer_id = entry.get("customer_id")
        amount = entry.get("amount")
        if not customer_id or not amount:
            continue
        payment_mode = entry.get("payment_mode", "CASH")
        if product == "iop" and idx in split_of:
            split = split_of[idx]
            txn = svc.upsert(customer_id, collection_date, amount - float(split["principal"]), payment_mode, True, split["principal"])
            principal_added += float(split["delta"])
        else:
            txn = svc.upsert(customer_id, collection_date, amount, payment_mode)
        results.append(txn)

    answer = {"submitted": len(results), "transactions": results}
    if principal_added:                           # only when a split moved principal_paid: the answer is unchanged otherwise
        answer["principal_added"] = principal_added
    return answer


def _split_record(entry: dict, day: Date) -> SimpleNamespace:
    """A voice entry in the shape the IOP split planner reads."""
    return SimpleNamespace(
        customer_id=entry.get("customer_id"), collection_date=day, payment_mode=entry.get("payment_mode", "CASH"),
        amount=entry.get("amount"), is_paid=True, product_type="IOP", principal_amount=entry.get("principal_amount") or 0,
    )


@router.post("/transcribe-online")
async def transcribe_online(
    audio: UploadFile = File(...),
    product: str = Form(...),
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """Transcribe online-payer mic recording and return matched entries."""
    if product not in ("edi", "iop"):
        raise HTTPException(status_code=400, detail="product must be 'edi' or 'iop'")
    audio_bytes = await audio.read()
    suffix = "." + (audio.filename or "audio.webm").rsplit(".", 1)[-1]
    svc = VoiceService(session)
    transcription = await run_in_threadpool(svc.transcribe, audio_bytes, suffix, product)
    matched = await run_in_threadpool(svc.match_online_entries, transcription, product)
    return {"transcription": transcription, "entries": matched}


@router.post("/detect-online")
def detect_online(
    transcription: str = Body(...),
    product: str = Body(...),
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """
    Re-parse an existing transcription to find customer names
    associated with 'online' payment keyword.
    Returns list of matched customer IDs.
    """
    if product not in ("edi", "iop"):
        raise HTTPException(status_code=400, detail="product must be 'edi' or 'iop'")

    svc = VoiceService(session)
    customer_ids = svc.detect_online_payments(transcription, product)
    return {"customer_ids": customer_ids}
