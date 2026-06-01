"""Подключение к SQLite."""
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Iterator

from backend.config import SQLITE_PATH

DB_PATH = Path(SQLITE_PATH)
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()