"""Бизнес-логика веб-кабинета: верификация, правка показателей и заключений,
замена файла-исходника, массовое удаление, повторное распознавание."""
from __future__ import annotations

import hashlib
import json
import sqlite3

from fastapi import BackgroundTasks, HTTPException

from botkin.config import UPLOAD_ALLOWED_EXTENSIONS, UPLOAD_MAX_BYTES
from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, LabRepo, ReportRepo, UserRepo
from botkin.pipeline.orchestrator import process_document
from botkin.preprocess.formats import resolve_extension
from botkin.storage import delete_quietly, is_stored_file, open_local, storage_for


def get_telegram_id(conn: sqlite3.Connection, user_id: int) -> int:
    """telegram_user_id пользователя для уведомлений; 0 если нет Telegram-аккаунта."""
    row = UserRepo(conn).get(user_id)
    return row.get("telegram_user_id") or 0 if row else 0


def loads_list(raw: str | None) -> list[str]:
    """JSON-колонка из БД → список; пусто/мусор → пустой список."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def require_own_document(conn: sqlite3.Connection, user_id: int, document_id: int) -> dict:
    doc = DocumentRepo(conn, user_id).get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    return doc


def add_lab(document_id: int, fields: dict, user_id: int) -> dict:
    if not fields.get("analyte_name"):
        raise HTTPException(status_code=422, detail="analyte_name обязателен")
    with get_conn() as conn:
        require_own_document(conn, user_id, document_id)
        repo = LabRepo(conn, user_id)
        lab_id = repo.insert_manual(document_id, fields)
        DocumentRepo(conn, user_id).clear_verified(document_id)
        return repo.get_row(lab_id)


def edit_lab(document_id: int, lab_id: int, fields: dict, user_id: int) -> dict:
    with get_conn() as conn:
        require_own_document(conn, user_id, document_id)
        repo = LabRepo(conn, user_id)
        row = repo.get_row(lab_id)
        if not row or row["document_id"] != document_id:
            raise HTTPException(status_code=404, detail="Показатель не найден")
        repo.update_row(lab_id, fields)
        DocumentRepo(conn, user_id).clear_verified(document_id)
        return repo.get_row(lab_id)


def delete_lab(document_id: int, lab_id: int, user_id: int) -> dict:
    with get_conn() as conn:
        require_own_document(conn, user_id, document_id)
        repo = LabRepo(conn, user_id)
        row = repo.get_row(lab_id)
        if not row or row["document_id"] != document_id:
            raise HTTPException(status_code=404, detail="Показатель не найден")
        repo.delete_row(lab_id)
        DocumentRepo(conn, user_id).clear_verified(document_id)
    return {"deleted": 1}


def edit_report(document_id: int, report_id: int, fields: dict, user_id: int) -> dict:
    with get_conn() as conn:
        require_own_document(conn, user_id, document_id)
        repo = ReportRepo(conn, user_id)
        if not repo.update_row(report_id, fields):
            raise HTTPException(status_code=404, detail="Заключение не найдено")
        DocumentRepo(conn, user_id).clear_verified(document_id)
        rows = [r for r in repo.for_document(document_id) if r["id"] == report_id]
    return rows[0] if rows else {"id": report_id}


def delete_document(document_id: int, user_id: int) -> dict:
    with get_conn() as conn:
        source_path = DocumentRepo(conn, user_id).delete(document_id)
    if source_path is None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    delete_quietly(source_path)
    return {"deleted": 1}


def delete_batch(ids: list[int], user_id: int) -> dict:
    deleted = 0
    with get_conn() as conn:
        repo = DocumentRepo(conn, user_id)
        for doc_id in ids:
            source_path = repo.delete(doc_id)
            if source_path is not None:
                delete_quietly(source_path)
                deleted += 1
    return {"deleted": deleted}


def verify_document(document_id: int, user_id: int) -> dict:
    with get_conn() as conn:
        repo = DocumentRepo(conn, user_id)
        if not repo.mark_verified(document_id):
            raise HTTPException(status_code=404, detail="Документ не найден")
        doc = repo.get(document_id)
    return {"document_id": document_id, "verified_at": doc["verified_at"]}


def replace_source(
    document_id: int,
    filename: str,
    body: bytes,
    background_tasks: BackgroundTasks,
    user_id: int,
) -> dict:
    if len(body) > UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large: {len(body)} bytes")
    if not body:
        raise HTTPException(status_code=400, detail="Empty file")
    if resolve_extension(filename, body[:32], UPLOAD_ALLOWED_EXTENSIONS) is None:
        raise HTTPException(status_code=415, detail="Unsupported file content")

    new_sha = hashlib.sha256(body).hexdigest()
    with get_conn() as conn:
        repo = DocumentRepo(conn, user_id)
        doc = require_own_document(conn, user_id, document_id)
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
        repo.clear_extracted_data(document_id)
        repo.clear_verified(document_id)
        tg_id = get_telegram_id(conn, user_id)
    background_tasks.add_task(process_document, document_id, tg_id)
    return {"document_id": document_id, "status": "received", "file_sha256": new_sha}


def reparse_document(
    document_id: int,
    background_tasks: BackgroundTasks,
    user_id: int,
) -> dict:
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
        tg_id = get_telegram_id(conn, user_id)
    background_tasks.add_task(process_document, document_id, tg_id)
    return {"document_id": document_id, "status": "received"}
