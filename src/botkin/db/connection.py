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
    "health_accounts": {
        # частота автосинка (часы); NULL = только вручную
        "sync_interval_hours": "INTEGER",
    },
    "users": {
        # CHECK через ALTER в SQLite не добавить — инвариант ролей держит UserRepo.
        "role": "TEXT NOT NULL DEFAULT 'user'",
        "display_name": "TEXT",
    },
    "documents": {
        "file_sha256": "TEXT",
        "raw_extraction": "TEXT",
        "title": "TEXT",
        "clinic": "TEXT",
        "delivered_at": "TIMESTAMP",
        # unix-время входа в текущую стадию — для достоверного прогресс-бара
        "stage_started_at": "REAL",
        # когда пользователь подтвердил корректность распознанных данных (верификация)
        "verified_at": "TIMESTAMP",
    },
    "lab_results": {
        "value_raw": "TEXT", "unit_raw": "TEXT", "taken_at_raw": "TEXT",
        "ref_operator": "TEXT",
        "ref_text": "TEXT",
        "analyte_canonical": "TEXT",
        "loinc": "TEXT",
        "nmu_code": "TEXT",
        "analyte_group": "TEXT",
        "match_status": "TEXT",
        "unit_expected": "TEXT",
        "unit_mismatch": "INTEGER",
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


def _drop_prescriptions(conn: sqlite3.Connection) -> None:
    """Тип prescription снят с поддержки — удаляем таблицу из старых БД."""
    conn.execute("DROP TABLE IF EXISTS prescriptions")
    conn.commit()


def _migrate_documents_schema(conn: sqlite3.Connection) -> None:
    """Пересоздаёт documents, если CHECK не содержит новых стадий или ещё допускает prescription."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='documents'"
    ).fetchone()
    if not row:
        return
    sql = row["sql"] or ""
    if "recognizing" in sql and "'prescription'" not in sql:
        return  # свежая схема или уже мигрировано

    new_ddl = """
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        doc_type TEXT CHECK(doc_type IN ('analysis','doctor_report','certificate','unknown')),
        source_path TEXT NOT NULL,
        raw_text TEXT,
        status TEXT NOT NULL DEFAULT 'received'
            CHECK(status IN ('received','processing','recognizing','normalizing','extracted','failed')),
        confidence REAL,
        raw_extraction TEXT,
        title TEXT,
        clinic TEXT,
        delivered_at TIMESTAMP,
        stage_started_at REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    new_cols = ["id", "user_id", "doc_type", "source_path", "raw_text", "status",
                "confidence", "raw_extraction", "title", "clinic", "delivered_at",
                "stage_started_at", "created_at"]
    old_cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
    shared = ", ".join(c for c in new_cols if c in old_cols)

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("ALTER TABLE documents RENAME TO _documents_old")
    # legacy-рецепты больше не валидны под новым CHECK — переразмечаем в unknown.
    conn.execute("UPDATE _documents_old SET doc_type='unknown' WHERE doc_type='prescription'")
    conn.executescript(new_ddl)
    conn.execute(f"INSERT INTO documents ({shared}) SELECT {shared} FROM _documents_old")
    conn.execute("DROP TABLE _documents_old")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_user_created ON documents(user_id, created_at)")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


def _migrate_rag_chunks_schema(conn: sqlite3.Connection) -> None:
    """Пересоздаёт rag_chunks, если CHECK(source) ещё не допускает 'research'.

    id сохраняем явно: rag_vectors (vec0) ссылается на rag_chunks.id, и потеря id
    осиротила бы вектора. Порядок вставки с исходным id — единственный корректный путь,
    ALTER CHECK в SQLite не поддерживается.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='rag_chunks'"
    ).fetchone()
    if not row or "'research'" in (row["sql"] or ""):
        return  # таблицы нет (создастся из schema.sql) или уже мигрировано

    new_ddl = """
    CREATE TABLE rag_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL CHECK(source IN ('drugs','analytes','health','research')),
        user_id INTEGER,
        ref_key TEXT NOT NULL,
        text TEXT NOT NULL,
        meta_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(source, ref_key)
    )
    """
    cols = "id, source, user_id, ref_key, text, meta_json, created_at"
    conn.execute("ALTER TABLE rag_chunks RENAME TO _rag_chunks_old")
    conn.executescript(new_ddl)
    conn.execute(f"INSERT INTO rag_chunks ({cols}) SELECT {cols} FROM _rag_chunks_old")
    conn.execute("DROP TABLE _rag_chunks_old")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_source ON rag_chunks(source)")
    conn.commit()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        _apply_migrations(conn)
        _migrate_documents_schema(conn)
        _migrate_rag_chunks_schema(conn)
        _drop_prescriptions(conn)


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Переопределяем встроенную LOWER на Python-variant: SQLite без ICU не опускает
    # регистр для non-ASCII (кириллица). str.lower() — суперсет ASCII-lower, поэтому
    # существующие запросы на латинице продолжат работать, а поиск по кириллице
    # (search-фильтр кабинета, dynamics) станет регистронезависимым.
    conn.create_function("lower", 1, lambda s: s.lower() if isinstance(s, str) else s)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Явная транзакция поверх autocommit-коннекта: либо всё, либо ничего.

    get_conn() работает в autocommit (isolation_level=None), поэтому каждая отдельная
    INSERT фиксируется сразу. Для вставки панели целиком нужна ручная BEGIN/COMMIT —
    при сбое на середине откатываемся, а не оставляем половину строк.
    """
    conn.execute("BEGIN")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")