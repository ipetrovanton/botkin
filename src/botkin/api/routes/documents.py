"""API веб-кабинета: пользователь, лента документов, детальная карточка, статус."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_user_id
from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, LabRepo, ReportRepo

router = APIRouter(prefix="/api", tags=["cabinet"])


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
