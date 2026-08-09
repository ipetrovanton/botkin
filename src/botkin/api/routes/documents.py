"""API веб-кабинета: пользователь, лента документов, детальная карточка, статус,
исходники, удаление и повторное распознавание.

Тонкие роуты — HTTP-слой (параметры → вызов сервиса → ответ).
Бизнес-логика в api/services/documents.py.
"""
import json
import mimetypes

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..deps import get_user_id
from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, LabRepo, ReportRepo
from botkin.pipeline.progress_model import StageDurationStore, estimate_progress
from botkin.pipeline.queue import LLM_QUEUE
from botkin.storage import is_stored_file, open_local, storage_for

from botkin.api.services.documents import (
    add_lab,
    delete_batch,
    delete_document,
    delete_lab,
    edit_lab,
    edit_report,
    loads_list,
    process_document,  # noqa: F401 — re-export для тестов, патчащих botkin.api.routes.documents
    reparse_document,
    replace_source,
    require_own_document,
    verify_document,
)

router = APIRouter(prefix="/api", tags=["cabinet"])

_EXTRA_MEDIA_TYPES = {".heic": "image/heic", ".heif": "image/heif"}


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
                    "recommendations": loads_list(r["recommendations_json"]),
                    "complaints": loads_list(r["complaints_json"]),
                    "medications": loads_list(r["medications_json"]),
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
        "queue_position": LLM_QUEUE.position(document_id),
        "queue_waiting": LLM_QUEUE.snapshot()["waiting"],
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
def delete_document_route(document_id: int, user_id: int = Depends(get_user_id)) -> dict:
    """Полное удаление: документ, показатели, заключения, файл-исходник."""
    return delete_document(document_id, user_id)


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


@router.post("/documents/{document_id}/labs", status_code=201)
def add_document_lab(
    document_id: int, req: LabEditRequest, user_id: int = Depends(get_user_id),
) -> dict:
    """Ручное добавление показателя, пропущенного распознаванием."""
    return add_lab(document_id, req.set_fields(), user_id)


@router.patch("/documents/{document_id}/labs/{lab_id}")
def edit_document_lab(
    document_id: int, lab_id: int, req: LabEditRequest,
    user_id: int = Depends(get_user_id),
) -> dict:
    return edit_lab(document_id, lab_id, req.set_fields(), user_id)


@router.delete("/documents/{document_id}/labs/{lab_id}")
def delete_document_lab(
    document_id: int, lab_id: int, user_id: int = Depends(get_user_id),
) -> dict:
    return delete_lab(document_id, lab_id, user_id)


@router.patch("/documents/{document_id}/reports/{report_id}")
def edit_document_report(
    document_id: int, report_id: int, req: ReportEditRequest,
    user_id: int = Depends(get_user_id),
) -> dict:
    return edit_report(document_id, report_id, req.set_fields(), user_id)


@router.get("/documents/{document_id}/versions")
def document_versions(document_id: int, user_id: int = Depends(get_user_id)) -> dict:
    """История версий файла-исходника (появляются после замены файла)."""
    with get_conn() as conn:
        doc = require_own_document(conn, user_id, document_id)
    if not is_stored_file(doc["source_path"]):
        return {"items": []}
    return {"items": storage_for(doc["source_path"]).versions(doc["source_path"])}


@router.post("/documents/{document_id}/replace")
async def replace_document_source(
    document_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: int = Depends(get_user_id),
) -> dict:
    """Замена файла-исходника новой версией (переснятый бланк) + перераспознавание."""
    body = await file.read()
    return replace_source(document_id, file.filename, body, background_tasks, user_id)


@router.post("/documents/{document_id}/verify")
def verify_document_route(document_id: int, user_id: int = Depends(get_user_id)) -> dict:
    """Пользователь подтверждает: распознанные данные соответствуют оригиналу."""
    return verify_document(document_id, user_id)


class DeleteBatchRequest(BaseModel):
    ids: list[int]


@router.post("/documents/delete-batch")
def delete_documents_batch(
    payload: DeleteBatchRequest, user_id: int = Depends(get_user_id),
) -> dict:
    """Массовое удаление. Чужие и несуществующие id тихо пропускаются."""
    return delete_batch(payload.ids, user_id)


@router.post("/documents/{document_id}/reparse")
def reparse_document_route(
    document_id: int,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(get_user_id),
) -> dict:
    """Повторное распознавание: очистка извлечённых данных + перезапуск pipeline."""
    return reparse_document(document_id, background_tasks, user_id)
