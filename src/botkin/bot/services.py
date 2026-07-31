"""Сервисный слой бота: изоляция DB-вызовов от хендлеров.

Хендлеры (bot/handlers/*.py) остаются тонкими — parse → service → render.
Все обращения к репозиториям концентрируются здесь.
"""
from __future__ import annotations

from datetime import datetime

from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, LabRepo, UserRepo


def resolve_user(tg_user_id: int) -> int | None:
    """user_id по telegram_user_id или None, если пользователь не зарегистрирован."""
    with get_conn() as conn:
        return UserRepo(conn).get_id(tg_user_id)


def resolve_user_or_create(tg_user_id: int) -> int:
    """user_id по telegram_user_id, регистрируя нового при необходимости."""
    with get_conn() as conn:
        return UserRepo(conn).get_or_create(tg_user_id)


def get_last_document(user_id: int) -> dict | None:
    with get_conn() as conn:
        return DocumentRepo(conn, user_id).get_last()


def get_document(doc_id: int, user_id: int) -> dict | None:
    with get_conn() as conn:
        return DocumentRepo(conn, user_id).get(doc_id)


def get_document_status(doc_id: int, user_id: int) -> str:
    with get_conn() as conn:
        return DocumentRepo(conn, user_id).get_status(doc_id)


def claim_delivery(doc_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        return DocumentRepo(conn, user_id).claim_delivery(doc_id)


def get_adjacent_id(doc_id: int, user_id: int, *, older: bool) -> int | None:
    with get_conn() as conn:
        return DocumentRepo(conn, user_id).adjacent_id(doc_id, older=older)


def list_documents(
    user_id: int, *, doc_type: str | None = None, limit: int = 10, offset: int = 0,
) -> tuple[int, list[dict]]:
    with get_conn() as conn:
        repo = DocumentRepo(conn, user_id)
        total = repo.count(doc_type=doc_type)
        docs = repo.list(doc_type=doc_type, limit=limit, offset=offset)
    return total, docs


def list_period_docs(
    user_id: int, start: datetime, end: datetime, *, limit: int = 10,
) -> list[dict]:
    with get_conn() as conn:
        return DocumentRepo(conn, user_id).in_period(start, end, limit=limit, offset=0)


def get_dynamics(user_id: int, analyte: str, *, limit: int = 30) -> list[dict]:
    with get_conn() as conn:
        return LabRepo(conn, user_id).dynamics(analyte, limit=limit)


def get_period_labs(user_id: int, start: datetime, end: datetime) -> list:
    with get_conn() as conn:
        return LabRepo(conn, user_id).in_period(start, end)
