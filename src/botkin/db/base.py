"""Базовый класс репозитория с tenant-скоупом."""
from __future__ import annotations

import sqlite3


class BaseRepo:
    """Все репозитории получают user_id в конструкторе."""

    table: str = ""

    def __init__(self, conn: sqlite3.Connection, user_id: int) -> None:
        if user_id <= 0:
            raise ValueError("user_id обязателен и должен быть > 0")
        self.conn = conn
        self.user_id = user_id
