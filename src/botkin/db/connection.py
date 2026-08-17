"""Подключение к SQLite."""
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from botkin.config import SQLITE_PATH

DB_PATH = Path(SQLITE_PATH)
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Промежуточные имена, под которыми миграции пересоздают таблицы, и их реальные
# цели. Нужны для восстановления FK: см. _repair_dangling_foreign_keys.
_MIGRATION_TEMP_TABLES = {
    "_users_old": "users",
    "_documents_old": "documents",
    "_rag_chunks_old": "rag_chunks",
}


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
        # Веб-регистрация: email + пароль (пилот без подтверждения почты).
        "email": "TEXT",
        "password_hash": "TEXT",
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


def _copy_shared_columns(conn: sqlite3.Connection, source: str, target: str) -> None:
    """Переносит данные по колонкам, присутствующим в обеих таблицах.

    Список считается из PRAGMA, а не перечисляется вручную: захардкоженный
    перечень отстаёт от схемы, и добавленную в _MIGRATIONS колонку легко забыть
    в переносе. Так при пересоздании documents терялись file_sha256 (хеш
    дедупликации) и verified_at (подтверждение пользователем).
    """
    src = {r["name"] for r in conn.execute(f"PRAGMA table_info({source})").fetchall()}
    dst = {r["name"] for r in conn.execute(f"PRAGMA table_info({target})").fetchall()}
    columns = ", ".join(f'"{c}"' for c in sorted(src & dst))
    conn.execute(f"INSERT INTO {target} ({columns}) SELECT {columns} FROM {source}")


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


def _drop_profile_coordinates(conn: sqlite3.Connection) -> None:
    """Погодный блок снят — координаты пациенту больше не нужны.

    latitude/longitude заводились только под запрос погоды по месту жительства.
    DROP COLUMN доступен с SQLite 3.35; проверка по PRAGMA делает вызов
    идемпотентным, как и остальные миграции.
    """
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(patient_profile)").fetchall()}
    for column in ("latitude", "longitude"):
        if column in existing:
            conn.execute(f"ALTER TABLE patient_profile DROP COLUMN {column}")
    conn.commit()


def _migrate_users_schema(conn: sqlite3.Connection) -> None:
    """Пересоздаёт users, если telegram_user_id ещё NOT NULL — нужно для веб-регистрации
    без Telegram. Также переносит UNIQUE-индексы и добавляет sessions-таблицу.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if not row:
        return
    sql = row["sql"] or ""
    # NOT NULL UNIQUE на telegram_user_id — признак старой схемы.
    if "telegram_user_id INTEGER NOT NULL UNIQUE" not in sql:
        return  # уже мигрировано или свежая схема

    conn.execute("PRAGMA foreign_keys=OFF")
    # legacy_alter_table=ON обязателен: с SQLite 3.25 обычный RENAME переписывает
    # REFERENCES в дочерних таблицах вслед за переименованием, и после DROP
    # временной таблицы ссылки становятся висячими.
    conn.execute("PRAGMA legacy_alter_table=ON")
    conn.execute("ALTER TABLE users RENAME TO _users_old")
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER UNIQUE,
            email TEXT UNIQUE,
            password_hash TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            display_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Переносим все колонки, которые существуют в старой таблице.
    _copy_shared_columns(conn, "_users_old", "users")
    conn.execute("DROP TABLE _users_old")
    conn.execute("PRAGMA legacy_alter_table=OFF")
    conn.execute("PRAGMA foreign_keys=ON")
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
        verified_at TIMESTAMP,
        file_sha256 TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")  # см. _migrate_users_schema
    conn.execute("ALTER TABLE documents RENAME TO _documents_old")
    # legacy-рецепты больше не валидны под новым CHECK — переразмечаем в unknown.
    conn.execute("UPDATE _documents_old SET doc_type='unknown' WHERE doc_type='prescription'")
    conn.executescript(new_ddl)
    _copy_shared_columns(conn, "_documents_old", "documents")
    conn.execute("DROP TABLE _documents_old")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_user_created ON documents(user_id, created_at)")
    conn.execute("PRAGMA legacy_alter_table=OFF")
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
    conn.execute("PRAGMA legacy_alter_table=ON")  # см. _migrate_users_schema
    conn.execute("ALTER TABLE rag_chunks RENAME TO _rag_chunks_old")
    conn.executescript(new_ddl)
    # id входит в общие колонки — vec0-вектора ссылаются на rag_chunks.id.
    _copy_shared_columns(conn, "_rag_chunks_old", "rag_chunks")
    conn.execute("DROP TABLE _rag_chunks_old")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_source ON rag_chunks(source)")
    conn.execute("PRAGMA legacy_alter_table=OFF")
    conn.commit()


def _repair_dangling_foreign_keys(conn: sqlite3.Connection) -> None:
    """Возвращает FK дочерних таблиц на реальных родителей.

    БД, мигрированные до появления legacy_alter_table=ON, содержат ссылки на
    промежуточные _users_old/_documents_old, которых уже нет. Сейчас это не
    ломает работу (get_conn не включает foreign_keys), но делает ограничения
    бессмысленными: ON DELETE CASCADE у sessions не сработает, а включение
    проверки FK уронит вставки.

    Таблица пересоздаётся по своему же DDL с подменой только имени цели —
    состав колонок и ограничений не трогаем, поэтому починка не зависит от
    того, насколько схема успела разойтись со schema.sql.
    """
    tables = {r["name"]: r["sql"] for r in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()}

    broken: dict[str, set[str]] = {}
    for name in tables:
        targets = {r["table"] for r in conn.execute(
            f"PRAGMA foreign_key_list({name})").fetchall()}
        missing = {t for t in targets if t not in tables and t in _MIGRATION_TEMP_TABLES}
        if missing:
            broken[name] = missing
    if not broken:
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    # Правим строго по одной таблице, поэтому финальный RENAME не должен
    # переписывать ссылки в остальных — иначе получим новую порцию висячих FK.
    conn.execute("PRAGMA legacy_alter_table=ON")
    for name, missing in broken.items():
        sql = tables[name] or ""
        for temp_name in missing:
            real = _MIGRATION_TEMP_TABLES[temp_name]
            if real not in tables:
                continue  # родителя нет вовсе — не наш случай, оставляем как есть
            sql = sql.replace(f'"{temp_name}"', real).replace(f" {temp_name}(", f" {real}(")

        staging = f"{name}__fk_repair"
        conn.execute(re.sub(rf'^CREATE TABLE "?{re.escape(name)}"?',
                            f"CREATE TABLE {staging}", sql, count=1))
        columns = ", ".join(f'"{r["name"]}"' for r in conn.execute(
            f"PRAGMA table_info({name})").fetchall())
        conn.execute(f"INSERT INTO {staging} ({columns}) SELECT {columns} FROM {name}")
        # DROP TABLE уносит индексы таблицы — восстанавливаем их после переноса.
        index_ddl = [r["sql"] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name = ? "
            "AND sql IS NOT NULL", (name,)).fetchall()]
        conn.execute(f"DROP TABLE {name}")
        conn.execute(f"ALTER TABLE {staging} RENAME TO {name}")
        for ddl in index_ddl:
            conn.execute(ddl)
    conn.execute("PRAGMA legacy_alter_table=OFF")
    conn.commit()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        # Сначала добавляем колонки: пересоздающие миграции ниже переносят данные
        # по пересечению колонок, поэтому к их запуску исходная таблица должна
        # содержать всё, что есть в _MIGRATIONS.
        _apply_migrations(conn)
        _migrate_users_schema(conn)
        _migrate_documents_schema(conn)
        _migrate_rag_chunks_schema(conn)
        # Повторно и идемпотентно: DDL пересозданных таблиц захардкожен и может
        # отстать от _MIGRATIONS, а второй проход дописывает недостающие колонки
        # без ручной синхронизации двух списков.
        _apply_migrations(conn)
        _drop_prescriptions(conn)
        _drop_profile_coordinates(conn)
        # Последним: ремонт опирается на актуальный DDL, поэтому должен видеть
        # результат всех пересозданий и удалений колонок выше.
        _repair_dangling_foreign_keys(conn)


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