"""Подключение к SQLite."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from botkin.config import SQLITE_PATH

DB_PATH = Path(SQLITE_PATH)
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


# Колонки, добавляемые поверх существующих таблиц (идемпотентно).
_MIGRATIONS: dict[str, dict[str, str]] = {
    "documents": {
        "raw_extraction": "TEXT",
        "title": "TEXT",
        "clinic": "TEXT",
        "delivered_at": "TIMESTAMP",
    },
    "lab_results": {"value_raw": "TEXT", "unit_raw": "TEXT", "taken_at_raw": "TEXT"},
    "prescriptions": {
        "drug_raw": "TEXT", "match_status": "TEXT",
        "reg_statuses": "TEXT", "reg_numbers": "TEXT",
    },
    "doctor_reports": {"medications_normalized_json": "TEXT"},
}


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, columns in _MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, sql_type in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
    conn.commit()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        _apply_migrations(conn)


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()