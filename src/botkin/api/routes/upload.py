"""API загрузки документов."""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Header, HTTPException, UploadFile

from botkin.config import UPLOAD_ALLOWED_EXTENSIONS, UPLOAD_MAX_BYTES, UPLOAD_SOURCES_DIR
from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo
from botkin.domain.models import UploadResponse
from botkin.pipeline.orchestrator import process_document

from ..deps import get_user_id

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("", response_model=UploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: int = Depends(get_user_id),
    x_telegram_user_id: int = Header(..., alias="X-Telegram-User-Id"),
) -> UploadResponse:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in UPLOAD_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"Unsupported file ext: {ext}")

    body = await file.read()
    if len(body) > UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large: {len(body)} bytes")
    if not body:
        raise HTTPException(status_code=400, detail="Empty file")

    yyyy_mm = datetime.now(timezone.utc).strftime("%Y-%m")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dest_dir = UPLOAD_SOURCES_DIR / str(user_id) / yyyy_mm
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = (file.filename or "doc").replace("/", "_").replace("\\", "_")
    dest = dest_dir / f"{ts}-{safe_name}"
    dest.write_bytes(body)

    with get_conn() as conn:
        doc_id = DocumentRepo(conn, user_id).create(source_path=str(dest))

    background_tasks.add_task(process_document, doc_id, x_telegram_user_id)
    return UploadResponse(document_id=doc_id, status="received")