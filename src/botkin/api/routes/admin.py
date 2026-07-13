"""API администратора: управление пользователями и их анализами.

Все роуты защищены require_admin (403 для обычной роли). Демо-уровень
идентификации — по заголовку X-Telegram-User-Id; см. deps.require_admin.
"""
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from botkin.db.connection import get_conn
from botkin.db.repos import LabRepo, UserRepo

from ..deps import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ============ Пользователи ============


class UserCreateRequest(BaseModel):
    telegram_user_id: int = Field(..., gt=0)
    display_name: str | None = Field(None, max_length=200)
    role: str = Field("user", pattern="^(admin|user)$")


class UserPatchRequest(BaseModel):
    display_name: str | None = Field(None, max_length=200)
    role: str | None = Field(None, pattern="^(admin|user)$")


@router.get("/users")
def list_users(_admin: int = Depends(require_admin)) -> dict:
    with get_conn() as conn:
        return {"items": UserRepo(conn).list_all()}


@router.post("/users", status_code=201)
def create_user(req: UserCreateRequest, _admin: int = Depends(require_admin)) -> dict:
    with get_conn() as conn:
        repo = UserRepo(conn)
        try:
            user_id = repo.create(req.telegram_user_id, req.display_name, req.role)
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail=f"Пользователь с telegram_user_id={req.telegram_user_id} уже существует",
            )
        return repo.get(user_id)


@router.patch("/users/{user_id}")
def patch_user(
    user_id: int, req: UserPatchRequest, admin_id: int = Depends(require_admin),
) -> dict:
    with get_conn() as conn:
        repo = UserRepo(conn)
        user = repo.get(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        # Последний админ не может разжаловать сам себя — иначе админка недоступна навсегда.
        if req.role == "user" and user["role"] == "admin" and repo.admin_count() <= 1:
            raise HTTPException(status_code=409, detail="Нельзя разжаловать последнего администратора")
        if req.role is not None:
            repo.set_role(user_id, req.role)
        if "display_name" in req.model_fields_set:
            repo.set_display_name(user_id, req.display_name)
        return repo.get(user_id)


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin_id: int = Depends(require_admin)) -> dict:
    if user_id == admin_id:
        raise HTTPException(status_code=409, detail="Нельзя удалить самого себя")
    with get_conn() as conn:
        repo = UserRepo(conn)
        if not repo.get(user_id):
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        paths = repo.delete_cascade(user_id)
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass
    return {"deleted": 1, "files_removed": len(paths)}


# ============ Анализы пользователя ============


class LabFields(BaseModel):
    """Редактируемые поля показателя; None = не менять (PATCH) / пусто (POST)."""

    analyte_name: str | None = Field(None, max_length=300)
    value_num: float | None = None
    value_text: str | None = Field(None, max_length=300)
    unit: str | None = Field(None, max_length=50)
    ref_low: float | None = None
    ref_high: float | None = None
    ref_text: str | None = Field(None, max_length=200)
    taken_at: str | None = Field(None, max_length=30)

    def set_fields(self) -> dict:
        """Только явно переданные клиентом поля — PATCH не затирает остальные."""
        return {k: getattr(self, k) for k in self.model_fields_set}


def _labs_repo(conn, target_user_id: int) -> LabRepo:
    if not UserRepo(conn).get(target_user_id):
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return LabRepo(conn, target_user_id)


@router.get("/users/{user_id}/labs")
def list_labs(
    user_id: int,
    q: str | None = Query(None, description="фильтр по названию показателя"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: int = Depends(require_admin),
) -> dict:
    with get_conn() as conn:
        _labs_repo(conn, user_id)  # проверка существования пользователя
        where = "user_id = ?"
        params: list = [user_id]
        if q:
            where += " AND LOWER(COALESCE(analyte_canonical, analyte_name)) LIKE ?"
            params.append(f"%{q.lower()}%")
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM lab_results WHERE {where}", tuple(params)
        ).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM lab_results WHERE {where} "
            "ORDER BY taken_at DESC, id DESC LIMIT ? OFFSET ?",
            tuple(params + [limit, offset]),
        ).fetchall()
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.post("/users/{user_id}/labs", status_code=201)
def create_lab(
    user_id: int,
    req: LabFields,
    document_id: int | None = Query(None, description="привязка к документу; без него — служебный ручной документ"),
    _admin: int = Depends(require_admin),
) -> dict:
    fields = req.set_fields()
    if not fields.get("analyte_name"):
        raise HTTPException(status_code=422, detail="analyte_name обязателен")
    with get_conn() as conn:
        repo = _labs_repo(conn, user_id)
        doc_id = document_id or _manual_document(conn, user_id)
        lab_id = repo.insert_manual(doc_id, fields)
        return repo.get_row(lab_id)


@router.patch("/labs/{lab_id}")
def patch_lab(
    lab_id: int,
    req: LabFields,
    user_id: int = Query(..., description="владелец показателя"),
    _admin: int = Depends(require_admin),
) -> dict:
    with get_conn() as conn:
        repo = _labs_repo(conn, user_id)
        if not repo.update_row(lab_id, req.set_fields()):
            raise HTTPException(status_code=404, detail="Показатель не найден")
        return repo.get_row(lab_id)


@router.delete("/labs/{lab_id}")
def delete_lab(
    lab_id: int,
    user_id: int = Query(..., description="владелец показателя"),
    _admin: int = Depends(require_admin),
) -> dict:
    with get_conn() as conn:
        if not _labs_repo(conn, user_id).delete_row(lab_id):
            raise HTTPException(status_code=404, detail="Показатель не найден")
    return {"deleted": 1}


def _manual_document(conn, user_id: int) -> int:
    """Служебный документ «ручной ввод»: lab_results.document_id NOT NULL,
    а показатель, добавленный админом без бланка, ни к какому файлу не привязан.
    Один такой документ на пользователя, создаётся лениво."""
    row = conn.execute(
        "SELECT id FROM documents WHERE user_id = ? AND source_path = 'manual://admin'",
        (user_id,),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO documents(user_id, doc_type, source_path, status, title) "
        "VALUES (?, 'analysis', 'manual://admin', 'extracted', 'Ручной ввод (админ)')",
        (user_id,),
    )
    conn.commit()
    return cur.lastrowid
