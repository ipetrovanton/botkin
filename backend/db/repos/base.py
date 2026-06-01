"""Базовый класс репозитория с обязательной user_id-фильтрацией."""
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