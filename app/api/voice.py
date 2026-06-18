from datetime import date as Date

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
    Each entry: {customer_id, amount}
    """
    if product not in ("edi", "iop"):
        raise HTTPException(status_code=400, detail="product must be 'edi' or 'iop'")

    results = []
    if product == "edi":
        svc = EdiTransactionService(session)
    else:
        svc = IopTransactionService(session)

    for entry in entries:
        customer_id = entry.get("customer_id")
        amount = entry.get("amount")
        if not customer_id or not amount:
            continue
        payment_mode = entry.get("payment_mode", "CASH")
        txn = svc.upsert(customer_id, collection_date, amount, payment_mode)
        results.append(txn)

    return {"submitted": len(results), "transactions": results}


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
