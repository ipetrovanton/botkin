"""API веб-кабинета: пользователь, лента документов, детальная карточка, статус,
исходники, удаление и повторное распознавание."""
import hashlib
import json
import mimetypes

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..deps import get_telegram_user_id, get_user_id
from botkin.config import UPLOAD_ALLOWED_EXTENSIONS, UPLOAD_MAX_BYTES
from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, LabRepo, ReportRepo
from botkin.pipeline.orchestrator import process_document
from botkin.pipeline.progress_model import StageDurationStore, estimate_progress
from botkin.preprocess.formats import resolve_extension
from botkin.storage import delete_quietly, is_stored_file, open_local, storage_for

router = APIRouter(prefix="/api", tags=["cabinet"])

# iPhone-фото: стандартный mimetypes может не знать HEIC/HEIF.
_EXTRA_MEDIA_TYPES = {".heic": "image/heic", ".heif": "image/heif"}


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
            "SELECT id, telegram_user_id, role, display_name, created_at "
            "FROM users WHERE id = ?", (user_id,)
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
                    "id": r["id"],
                    "diagnosis": r["diagnosis"],
                    "doctor_name": r["doctor_name"],
                    "department": r["department"],
                    "visit_date": r["visit_date"],
                    "recommendations": _loads_list(r["recommendations_json"]),
                    "complaints": _loads_list(r["complaints_json"]),
                    "medications": _loads_list(r["medications_json"]),
                }
                for r in rows
            ]
    return {"document": doc, "kind": kind, "labs": labs, "reports": reports}


@router.get("/documents/{document_id}/status")
def document_status(document_id: int, user_id: int = Depends(get_user_id)) -> dict:
    """Статус + достоверный прогресс — для поллинга из веб-кабинета и бота.

    percent растёт внутри длинной VLM-стадии (по исторической EMA длительности),
    поэтому фронт может показывать живое движение бара вместо замершей стадии.
    alive=false только при status=failed — «модель думает, а не упала».
    """
    with get_conn() as conn:
        repo = DocumentRepo(conn, user_id)
        row = repo.get_progress_row(document_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Документ не найден")
        est = estimate_progress(
            row["status"], row.get("stage_started_at"), StageDurationStore(conn),
        )
    return {
        "status": est.status,
        "percent": est.percent,
        "eta_seconds": est.eta_seconds,
        "stage_elapsed_s": est.stage_elapsed_s,
        "alive": est.alive,
    }


@router.get("/documents/{document_id}/source")
def document_source(document_id: int, user_id: int = Depends(get_user_id)) -> FileResponse:
    """Файл-исходник документа: пациент видит оригинал, а не только извлечённые данные."""
    with get_conn() as conn:
        doc = DocumentRepo(conn, user_id).get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    path = open_local(doc["source_path"])
    if path is None:
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
    delete_quietly(source_path)
    return {"deleted": 1}


# ===== Верификация распознанного (этап 2) =====
# Пользователь проверяет и правит извлечённые данные своего документа.
# Любая правка сбрасывает verified_at: подтверждение относилось к старой версии данных.


class LabEditRequest(BaseModel):
    """Редактируемые поля показателя; учитываются только явно переданные."""

    analyte_name: str | None = Field(None, max_length=300)
    value_num: float | None = None
    value_text: str | None = Field(None, max_length=300)
    unit: str | None = Field(None, max_length=50)
    ref_low: float | None = None
    ref_high: float | None = None
    ref_text: str | None = Field(None, max_length=200)
    taken_at: str | None = Field(None, max_length=30)

    def set_fields(self) -> dict:
        return {k: getattr(self, k) for k in self.model_fields_set}


class ReportEditRequest(BaseModel):
    """Правка заключения: списки приходят как list[str], в БД лежат JSON-колонки."""

    diagnosis: str | None = Field(None, max_length=4000)
    doctor_name: str | None = Field(None, max_length=200)
    department: str | None = Field(None, max_length=200)
    recommendations: list[str] | None = None
    medications: list[str] | None = None

    def set_fields(self) -> dict:
        fields: dict = {}
        for key in ("diagnosis", "doctor_name", "department"):
            if key in self.model_fields_set:
                fields[key] = getattr(self, key)
        for key, column in (("recommendations", "recommendations_json"),
                            ("medications", "medications_json")):
            if key in self.model_fields_set:
                fields[column] = json.dumps(getattr(self, key) or [], ensure_ascii=False)
        return fields


def _require_own_document(conn, user_id: int, document_id: int) -> dict:
    doc = DocumentRepo(conn, user_id).get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    return doc


@router.post("/documents/{document_id}/labs", status_code=201)
def add_document_lab(
    document_id: int, req: LabEditRequest, user_id: int = Depends(get_user_id),
) -> dict:
    """Ручное добавление показателя, пропущенного распознаванием."""
    fields = req.set_fields()
    if not fields.get("analyte_name"):
        raise HTTPException(status_code=422, detail="analyte_name обязателен")
    with get_conn() as conn:
        _require_own_document(conn, user_id, document_id)
        repo = LabRepo(conn, user_id)
        lab_id = repo.insert_manual(document_id, fields)
        DocumentRepo(conn, user_id).clear_verified(document_id)
        return repo.get_row(lab_id)


@router.patch("/documents/{document_id}/labs/{lab_id}")
def edit_document_lab(
    document_id: int, lab_id: int, req: LabEditRequest,
    user_id: int = Depends(get_user_id),
) -> dict:
    with get_conn() as conn:
        _require_own_document(conn, user_id, document_id)
        repo = LabRepo(conn, user_id)
        row = repo.get_row(lab_id)
        if not row or row["document_id"] != document_id:
            raise HTTPException(status_code=404, detail="Показатель не найден")
        repo.update_row(lab_id, req.set_fields())
        DocumentRepo(conn, user_id).clear_verified(document_id)
        return repo.get_row(lab_id)


@router.delete("/documents/{document_id}/labs/{lab_id}")
def delete_document_lab(
    document_id: int, lab_id: int, user_id: int = Depends(get_user_id),
) -> dict:
    with get_conn() as conn:
        _require_own_document(conn, user_id, document_id)
        repo = LabRepo(conn, user_id)
        row = repo.get_row(lab_id)
        if not row or row["document_id"] != document_id:
            raise HTTPException(status_code=404, detail="Показатель не найден")
        repo.delete_row(lab_id)
        DocumentRepo(conn, user_id).clear_verified(document_id)
    return {"deleted": 1}


@router.patch("/documents/{document_id}/reports/{report_id}")
def edit_document_report(
    document_id: int, report_id: int, req: ReportEditRequest,
    user_id: int = Depends(get_user_id),
) -> dict:
    with get_conn() as conn:
        _require_own_document(conn, user_id, document_id)
        repo = ReportRepo(conn, user_id)
        if not repo.update_row(report_id, req.set_fields()):
            raise HTTPException(status_code=404, detail="Заключение не найдено")
        DocumentRepo(conn, user_id).clear_verified(document_id)
        rows = [r for r in repo.for_document(document_id) if r["id"] == report_id]
    return rows[0] if rows else {"id": report_id}


@router.get("/documents/{document_id}/versions")
def document_versions(document_id: int, user_id: int = Depends(get_user_id)) -> dict:
    """История версий файла-исходника (появляются после замены файла)."""
    with get_conn() as conn:
        doc = _require_own_document(conn, user_id, document_id)
    if not is_stored_file(doc["source_path"]):
        return {"items": []}
    return {"items": storage_for(doc["source_path"]).versions(doc["source_path"])}


@router.post("/documents/{document_id}/replace")
async def replace_document_source(
    document_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: int = Depends(get_user_id),
    telegram_user_id: int = Depends(get_telegram_user_id),
) -> dict:
    """Замена файла-исходника новой версией (переснятый бланк) + перераспознавание.

    Валидация изменений: тот же контент-контроль, что и при загрузке (тип/размер),
    плюс запрет no-op замены тем же самым файлом (по sha256). Старая версия
    сохраняется бэкендом хранилища (versioning MinIO / .versions на диске).
    """
    body = await file.read()
    if len(body) > UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large: {len(body)} bytes")
    if not body:
        raise HTTPException(status_code=400, detail="Empty file")
    if resolve_extension(file.filename, body[:32], UPLOAD_ALLOWED_EXTENSIONS) is None:
        raise HTTPException(status_code=415, detail="Unsupported file content")

    new_sha = hashlib.sha256(body).hexdigest()
    with get_conn() as conn:
        repo = DocumentRepo(conn, user_id)
        doc = _require_own_document(conn, user_id, document_id)
        if not is_stored_file(doc["source_path"]):
            raise HTTPException(status_code=409, detail="У документа нет файла-исходника")
        if doc["file_sha256"] == new_sha:
            raise HTTPException(status_code=409, detail="Этот же файл уже загружен")
        storage_for(doc["source_path"]).replace(doc["source_path"], body)
        conn.execute(
            "UPDATE documents SET file_sha256 = ? WHERE id = ? AND user_id = ?",
            (new_sha, document_id, user_id),
        )
        conn.commit()
        # Новое содержимое — новое распознавание: старые данные относятся к старой версии.
        repo.clear_extracted_data(document_id)
        repo.clear_verified(document_id)
    background_tasks.add_task(process_document, document_id, telegram_user_id)
    return {"document_id": document_id, "status": "received", "file_sha256": new_sha}


@router.post("/documents/{document_id}/verify")
def verify_document(document_id: int, user_id: int = Depends(get_user_id)) -> dict:
    """Пользователь подтверждает: распознанные данные соответствуют оригиналу."""
    with get_conn() as conn:
        repo = DocumentRepo(conn, user_id)
        if not repo.mark_verified(document_id):
            raise HTTPException(status_code=404, detail="Документ не найден")
        doc = repo.get(document_id)
    return {"document_id": document_id, "verified_at": doc["verified_at"]}


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
                delete_quietly(source_path)
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
        if open_local(doc["source_path"]) is None:
            raise HTTPException(
                status_code=409,
                detail="Файл-исходник утрачен — повторное распознавание невозможно",
            )
        repo.clear_extracted_data(document_id)
    background_tasks.add_task(process_document, document_id, telegram_user_id)
    return {"document_id": document_id, "status": "received"}
