"""API веб-кабинета: пользователь, лента документов, детальная карточка, статус,
исходники, удаление и повторное распознавание."""
import json
import mimetypes
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..deps import get_telegram_user_id, get_user_id
from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, LabRepo, ReportRepo
from botkin.pipeline.orchestrator import process_document

router = APIRouter(prefix="/api", tags=["cabinet"])

# iPhone-фото: стандартный mimetypes может не знать HEIC/HEIF.
_EXTRA_MEDIA_TYPES = {".heic": "image/heic", ".heif": "image/heif"}


def _unlink_quietly(path_str: str | None) -> None:
    """Удаляет файл-исходник; отсутствие файла — не ошибка (мог быть утрачен)."""
    if not path_str:
        return
    try:
        Path(path_str).unlink(missing_ok=True)
    except OSError:
        pass


def _loads_list(raw: str | None) -> list[str]:
    """JSON-колонка из БД → список; пусто/мусор → пустой список."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


@router.get("/me")
def me(user_id: int = Depends(get_user_id)) -> dict:
    """Текущий пользователь кабинета (user_id + исходный telegram-идентификатор)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, telegram_user_id, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else {"id": user_id}


@router.get("/documents")
def list_documents(
    user_id: int = Depends(get_user_id),
    doc_type: str | None = Query(None),
    clinic: str | None = Query(None),
    doctor: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    status: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    """Фильтрованная лента документов с пагинацией."""
    with get_conn() as conn:
        items, total = DocumentRepo(conn, user_id).search(
            doc_type=doc_type, clinic=clinic, doctor=doctor,
            date_from=date_from, date_to=date_to, status=status, q=q,
            limit=limit, offset=offset,
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/documents/{document_id}")
def document_detail(document_id: int, user_id: int = Depends(get_user_id)) -> dict:
    """Документ + извлечённые данные (показатели либо заключения врача)."""
    with get_conn() as conn:
        repo = DocumentRepo(conn, user_id)
        doc = repo.get(document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Документ не найден")
        kind = doc["doc_type"]
        labs: list[dict] = []
        reports: list[dict] = []
        if kind == "analysis":
            labs = LabRepo(conn, user_id).for_document(document_id)
        elif kind == "doctor_report":
            rows = ReportRepo(conn, user_id).for_document(document_id)
            reports = [
                {
                    "diagnosis": r["diagnosis"],
                    "doctor_name": r["doctor_name"],
                    "department": r["department"],
                    "recommendations": _loads_list(r["recommendations_json"]),
                    "complaints": _loads_list(r["complaints_json"]),
                    "medications": _loads_list(r["medications_json"]),
                }
                for r in rows
            ]
    return {"document": doc, "kind": kind, "labs": labs, "reports": reports}


@router.get("/documents/{document_id}/status")
def document_status(document_id: int, user_id: int = Depends(get_user_id)) -> dict:
    """Текущий статус обработки — для поллинга прогресса из веб-кабинета."""
    with get_conn() as conn:
        status = DocumentRepo(conn, user_id).get_status(document_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    return {"status": status}


@router.get("/documents/{document_id}/source")
def document_source(document_id: int, user_id: int = Depends(get_user_id)) -> FileResponse:
    """Файл-исходник документа: пациент видит оригинал, а не только извлечённые данные."""
    with get_conn() as conn:
        doc = DocumentRepo(conn, user_id).get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    path = Path(doc["source_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Файл-исходник утрачен")
    media_type = (
        _EXTRA_MEDIA_TYPES.get(path.suffix.lower())
        or mimetypes.guess_type(path.name)[0]
        or "application/octet-stream"
    )
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.delete("/documents/{document_id}")
def delete_document(document_id: int, user_id: int = Depends(get_user_id)) -> dict:
    """Полное удаление: документ, показатели, заключения, файл-исходник.

    Данные исчезают из статистики и динамики сразу — других ссылок на них нет.
    """
    with get_conn() as conn:
        source_path = DocumentRepo(conn, user_id).delete(document_id)
    if source_path is None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    _unlink_quietly(source_path)
    return {"deleted": 1}


class DeleteBatchRequest(BaseModel):
    ids: list[int]


@router.post("/documents/delete-batch")
def delete_documents_batch(
    payload: DeleteBatchRequest, user_id: int = Depends(get_user_id),
) -> dict:
    """Массовое удаление. Чужие и несуществующие id тихо пропускаются."""
    deleted = 0
    with get_conn() as conn:
        repo = DocumentRepo(conn, user_id)
        for doc_id in payload.ids:
            source_path = repo.delete(doc_id)
            if source_path is not None:
                _unlink_quietly(source_path)
                deleted += 1
    return {"deleted": deleted}


@router.post("/documents/{document_id}/reparse")
def reparse_document(
    document_id: int,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(get_user_id),
    telegram_user_id: int = Depends(get_telegram_user_id),
) -> dict:
    """Повторное распознавание: очистка извлечённых данных + перезапуск pipeline.

    Обновление реализовано через полную очистку («удаление под капотом»):
    показатели и заключения стираются, статус сбрасывается в received,
    файл-исходник прогоняется через classify → extract заново.
    """
    with get_conn() as conn:
        repo = DocumentRepo(conn, user_id)
        doc = repo.get(document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Документ не найден")
        if not Path(doc["source_path"]).is_file():
            raise HTTPException(
                status_code=409,
                detail="Файл-исходник утрачен — повторное распознавание невозможно",
            )
        repo.clear_extracted_data(document_id)
    background_tasks.add_task(process_document, document_id, telegram_user_id)
    return {"document_id": document_id, "status": "received"}
