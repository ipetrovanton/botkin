"""API загрузки документов."""
import hashlib
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from botkin.config import UPLOAD_ALLOWED_EXTENSIONS, UPLOAD_MAX_BYTES
from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, UserRepo
from botkin.domain.models import UploadResponse
from botkin.pipeline.orchestrator import process_document
from botkin.preprocess.formats import resolve_extension
from botkin.storage import default_storage

from ..deps import get_user_id

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("", response_model=UploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: int = Depends(get_user_id),
) -> UploadResponse:
    body = await file.read()
    if len(body) > UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large: {len(body)} bytes")
    if not body:
        raise HTTPException(status_code=400, detail="Empty file")

    # Валидируем по содержимому: имя файла — лишь подсказка (iPhone шлёт HEIC без расширения).
    ext = resolve_extension(file.filename, body[:32], UPLOAD_ALLOWED_EXTENSIONS)
    if ext is None:
        raise HTTPException(status_code=415, detail="Unsupported file content")

    safe_name = (file.filename or "doc").replace("/", "_").replace("\\", "_")
    # Гарантируем корректное расширение: ниже по конвейеру PDF/изображение различаются по суффиксу.
    if Path(safe_name).suffix.lower() not in UPLOAD_ALLOWED_EXTENSIONS:
        safe_name = f"{safe_name}{ext}"
    # Бэкенд хранения (диск/MinIO) выбирается конфигом; uri уходит в source_path.
    uri = default_storage().save(user_id, safe_name, body)

    with get_conn() as conn:
        doc_id = DocumentRepo(conn, user_id).create(
            source_path=uri,
            # sha256 содержимого — точный признак повторной загрузки того же файла.
            file_sha256=hashlib.sha256(body).hexdigest(),
        )
        # telegram_user_id нужен для уведомлений в Telegram; у веб-пользователя может быть None.
        user_row = UserRepo(conn).get(user_id)
        tg_id = user_row.get("telegram_user_id") if user_row else 0

    background_tasks.add_task(process_document, doc_id, tg_id)
    return UploadResponse(document_id=doc_id, status="received")