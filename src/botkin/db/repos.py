"""Репозитории для работы с БД."""
from __future__ import annotations

import sqlite3


class BaseRepo:
    """Все репозитории получают user_id в конструкторе."""

    table: str = ""

    def __init__(self, conn: sqlite3.Connection, user_id: int):
        if user_id <= 0:
            raise ValueError("user_id обязателен и должен быть > 0")
        self.conn = conn
        self.user_id = user_id

    def _all_for_user(self, where_extra: str = "", params: tuple = ()) -> list[dict]:
        sql = f"SELECT * FROM {self.table} WHERE user_id = ?"
        if where_extra:
            sql += " AND " + where_extra
        rows = self.conn.execute(sql, (self.user_id, *params)).fetchall()
        return [dict(r) for r in rows]


class DocumentRepo(BaseRepo):
    table = "documents"

    def create(self, source_path: str, doc_type: str = "unknown") -> int:
        cur = self.conn.execute(
            "INSERT INTO documents(user_id, doc_type, source_path, status) "
            "VALUES (?, ?, ?, 'received')",
            (self.user_id, doc_type, source_path),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_status(self, document_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE documents SET status = ? WHERE id = ? AND user_id = ?",
            (status, document_id, self.user_id),
        )
        self.conn.commit()

    def set_doc_type(self, document_id: int, doc_type: str) -> None:
        self.conn.execute(
            "UPDATE documents SET doc_type = ? WHERE id = ? AND user_id = ?",
            (doc_type, document_id, self.user_id),
        )
        self.conn.commit()

    def mark_failed(self, document_id: int) -> None:
        self.set_status(document_id, "failed")

    def get(self, document_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE id = ? AND user_id = ?",
            (document_id, self.user_id),
        ).fetchone()
        return dict(row) if row else None

    def list_recent(self, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM documents WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (self.user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


class UserRepo(BaseRepo):
    table = "users"

    def get_or_create(self, telegram_user_id: int) -> int:
        """Возвращает user_id по telegram_user_id, создаёт при необходимости."""
        row = self.conn.execute(
            "SELECT id FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,),
        ).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO users(telegram_user_id) VALUES (?)",
            (telegram_user_id,),
        )
        self.conn.commit()
        return cur.lastrowid