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


def _migrate_documents_status_check(conn: sqlite3.Connection) -> None:
    """Пересоздаёт documents, если CHECK статусов не содержит новых стадий."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='documents'"
    ).fetchone()
    if not row or "recognizing" in (row["sql"] or ""):
        return  # свежая схема или уже мигрировано

    new_ddl = """
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        doc_type TEXT CHECK(doc_type IN ('analysis','prescription','doctor_report','certificate','unknown')),
        source_path TEXT NOT NULL,
        raw_text TEXT,
        status TEXT NOT NULL DEFAULT 'received'
            CHECK(status IN ('received','recognizing','normalizing','extracted','failed')),
        confidence REAL,
        raw_extraction TEXT,
        title TEXT,
        clinic TEXT,
        delivered_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    new_cols = ["id", "user_id", "doc_type", "source_path", "raw_text", "status",
                "confidence", "raw_extraction", "title", "clinic", "delivered_at", "created_at"]
    old_cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
    shared = ", ".join(c for c in new_cols if c in old_cols)

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("ALTER TABLE documents RENAME TO _documents_old")
    conn.executescript(new_ddl)
    conn.execute(f"INSERT INTO documents ({shared}) SELECT {shared} FROM _documents_old")
    conn.execute("DROP TABLE _documents_old")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        _apply_migrations(conn)
        _migrate_documents_status_check(conn)


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()