# Telegram UX (блок D) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Довести Telegram-UX до полноценного: живой прогресс обработки с авто-выдачей результата, навигация по документам inline-кнопками, запросы за период, корректный вывод ГРЛС и обогащение документов метаданными (название + клиника).

**Architecture:** Бот читает SQLite напрямую (как сейчас) и поллит статус документа для живого прогресс-бара; backend (orchestrator) пишет реальные стадии (`recognizing`/`normalizing`) и служит push-fallback'ом доставки. Навигация и период — на inline-кнопках с компактным `callback_data`. Метаданные `title`/`clinic` извлекаются расширенным `classify`.

**Tech Stack:** Python 3.12, aiogram 3, FastAPI, SQLite, instructor/Ollama (VLM, в тестах мокается), pytest, ruff.

**Спека:** `docs/superpowers/specs/2026-06-02-telegram-ux-design.md`

**Важно для исполнителя:**
- Dev-сервер БЕЗ GPU: VLM никогда не запускать, все тесты мокают LLM (`patch.object(orchestrator.classify, "run_vlm", return_value=...)`).
- Тестовая БД: фикстура `set_test_db` (в `tests/conftest.py`) подменяет `SQLITE_PATH`, перезагружает `config`+`connection`, вызывает `init_db()`. Все тесты с БД принимают параметр `set_test_db`.
- Прогон тестов: `uv run pytest -q`. Линт: `uv run ruff check .`.
- Каждая задача завершается коммитом. Сообщения коммитов — на русском, в стиле существующих (`feat:`, `fix:`, `test:`).

---

## Структура файлов

| Файл | Ответственность | Действие |
|---|---|---|
| `src/botkin/db/schema.sql` | каноническая схема (новые БД) | Modify |
| `src/botkin/db/connection.py` | миграции колонок + миграция CHECK статусов | Modify |
| `src/botkin/db/queries.py` | запросы для UI (документы, период, сводки) | Modify |
| `src/botkin/db/repos.py` | `DocumentRepo`: сохранение метаданных, захват доставки | Modify |
| `src/botkin/domain/models.py` | `ClassifyResult` += `title`, `clinic` | Modify |
| `src/botkin/llm/prompts.py` | `CLASSIFY_VLM_SYSTEM` += инструкции title/clinic | Modify |
| `src/botkin/llm/classify.py` | `ClassifySchema` + возврат title/clinic | Modify |
| `src/botkin/pipeline/orchestrator.py` | стадии статусов, сохранение метаданных, push-fallback | Modify |
| `src/botkin/pipeline/notifications.py` | формат финальной карточки для push-fallback | Modify |
| `src/botkin/bot/progress.py` | `render_progress(status)` + поллинг-логика | Create |
| `src/botkin/bot/keyboards.py` | кодирование/парсинг `callback_data`, сборка клавиатур | Create |
| `src/botkin/bot/cards.py` | общий рендер карточки документа (вынесено из `show.py`) | Create |
| `src/botkin/bot/period.py` | парсинг периода (пресеты + ручной ввод) | Create |
| `src/botkin/bot/handlers/browse.py` | `/list`, `/period`, callback-роутинг | Create |
| `src/botkin/bot/handlers/upload.py` | запуск поллинг-задачи после загрузки | Modify |
| `src/botkin/bot/handlers/show.py` | использовать общий рендер из `cards.py` | Modify |
| `src/botkin/bot/main.py` | регистрация роутеров и команд | Modify |

---

## Фаза 1 — БД: миграции и запросы

### Task 1: Колонки `title`, `clinic`, `delivered_at` + расширенный CHECK в схеме

**Files:**
- Modify: `src/botkin/db/schema.sql` (блок `CREATE TABLE documents`, стр. 17-28)
- Modify: `src/botkin/db/connection.py:14-22` (`_MIGRATIONS`)
- Test: `tests/test_migration.py`

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_migration.py`:

```python
def test_documents_has_new_columns(set_test_db):
    from botkin.db.connection import get_conn
    with get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
    assert {"title", "clinic", "delivered_at"} <= cols
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_migration.py::test_documents_has_new_columns -v`
Expected: FAIL (колонок нет)

- [ ] **Step 3: Обновить схему и миграции**

В `src/botkin/db/schema.sql`, блок `CREATE TABLE documents`, заменить определение `status` и добавить колонки:

```sql
    status TEXT NOT NULL DEFAULT 'received'
        CHECK(status IN ('received','recognizing','normalizing','extracted','failed')),
    confidence REAL,
    raw_extraction TEXT,
    title TEXT,
    clinic TEXT,
    delivered_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
```

В `src/botkin/db/connection.py` расширить `_MIGRATIONS["documents"]`:

```python
    "documents": {
        "raw_extraction": "TEXT",
        "title": "TEXT",
        "clinic": "TEXT",
        "delivered_at": "TIMESTAMP",
    },
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_migration.py::test_documents_has_new_columns -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/db/schema.sql src/botkin/db/connection.py tests/test_migration.py
git commit -m "feat(db): колонки title/clinic/delivered_at и стадии статусов в схеме"
```

---

### Task 2: Миграция CHECK статусов для существующих БД (пересоздание documents)

**Контекст:** `CREATE TABLE IF NOT EXISTS` не трогает существующую таблицу, а SQLite не меняет `CHECK` через `ALTER`. Поэтому для старых БД (со статусным CHECK без `recognizing`/`normalizing`) нужна явная миграция пересозданием.

**Files:**
- Modify: `src/botkin/db/connection.py`
- Test: `tests/test_migration.py`

- [ ] **Step 1: Написать падающий тест**

```python
def test_status_recognizing_allowed_after_migration(set_test_db):
    """На пересозданной таблице промежуточные статусы проходят CHECK."""
    from botkin.db.connection import get_conn
    from botkin.db.repos import DocumentRepo, UserRepo
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(555)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/a.jpg")
        DocumentRepo(conn, uid).set_status(did, "recognizing")  # не должно бросить
        row = conn.execute("SELECT status FROM documents WHERE id=?", (did,)).fetchone()
    assert row["status"] == "recognizing"


def test_legacy_check_table_migrated_preserving_data(tmp_path, monkeypatch):
    """Старая БД со статусным CHECK без новых стадий мигрируется, данные целы."""
    import sqlite3, importlib
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_user_id INTEGER);"
        "INSERT INTO users(telegram_user_id) VALUES (1);"
        "CREATE TABLE documents("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, doc_type TEXT,"
        " source_path TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'received'"
        " CHECK(status IN ('received','processing','extracted','failed')),"
        " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
        "INSERT INTO documents(user_id, doc_type, source_path, status)"
        " VALUES (1,'analysis','/tmp/legacy.jpg','extracted');"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("SQLITE_PATH", str(db))
    import botkin.config, botkin.db.connection
    importlib.reload(botkin.config)
    importlib.reload(botkin.db.connection)
    botkin.db.connection.init_db()

    with botkin.db.connection.get_conn() as c:
        row = c.execute("SELECT source_path, status FROM documents WHERE id=1").fetchone()
        sql = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='documents'"
        ).fetchone()["sql"]
    assert row["source_path"] == "/tmp/legacy.jpg"   # данные сохранены
    assert row["status"] == "extracted"
    assert "recognizing" in sql                       # CHECK расширен
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_migration.py::test_legacy_check_table_migrated_preserving_data -v`
Expected: FAIL (старый CHECK сохраняется, `recognizing` не появляется)

- [ ] **Step 3: Реализовать миграцию CHECK**

В `src/botkin/db/connection.py` добавить функцию и вызвать её из `init_db` ПОСЛЕ `_apply_migrations`:

```python
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
```

В `init_db` добавить вызов после `_apply_migrations(conn)`:

```python
        _apply_migrations(conn)
        _migrate_documents_status_check(conn)
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_migration.py -v`
Expected: PASS (оба новых теста + существующие)

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/db/connection.py tests/test_migration.py
git commit -m "feat(db): миграция CHECK статусов пересозданием documents с сохранением данных"
```

---

### Task 3: Запросы для UI в `queries.py`

**Files:**
- Modify: `src/botkin/db/queries.py`
- Test: `tests/test_queries_ui.py` (создать)

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_queries_ui.py`:

```python
from datetime import datetime
from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, UserRepo


def _seed(n=3, doc_type="analysis"):
    """Создаёт пользователя и n документов, возвращает (uid, [doc_id...])."""
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(42)
        ids = []
        for i in range(n):
            did = DocumentRepo(conn, uid).create(source_path=f"/tmp/{i}.jpg", doc_type=doc_type)
            ids.append(did)
    return uid, ids


def test_get_document_checks_owner(set_test_db):
    from botkin.db.queries import get_document
    uid, ids = _seed(1)
    assert get_document(ids[0], uid)["id"] == ids[0]
    assert get_document(ids[0], uid + 999) is None  # чужой — None


def test_get_document_status(set_test_db):
    from botkin.db.queries import get_document_status
    uid, ids = _seed(1)
    assert get_document_status(ids[0], uid) == "received"


def test_count_and_list_documents_with_filter_and_paging(set_test_db):
    from botkin.db.queries import count_documents, list_documents
    uid, _ = _seed(3, "analysis")
    with get_conn() as conn:
        DocumentRepo(conn, uid).create(source_path="/tmp/p.jpg", doc_type="prescription")
    assert count_documents(uid) == 4
    assert count_documents(uid, doc_type="analysis") == 3
    page = list_documents(uid, doc_type="analysis", limit=2, offset=0)
    assert len(page) == 2
    assert all(d["doc_type"] == "analysis" for d in page)


def test_documents_in_period(set_test_db):
    from botkin.db.queries import documents_in_period
    uid, ids = _seed(2)
    # обоим документам выставим дату в пределах периода
    with get_conn() as conn:
        conn.execute("UPDATE documents SET created_at='2026-05-10 10:00:00' WHERE id=?", (ids[0],))
        conn.execute("UPDATE documents SET created_at='2026-04-01 10:00:00' WHERE id=?", (ids[1],))
    res = documents_in_period(uid, datetime(2026, 5, 1), datetime(2026, 5, 31, 23, 59, 59))
    assert [d["id"] for d in res] == [ids[0]]


def test_labs_in_period_grouped(set_test_db):
    from botkin.db.queries import labs_in_period
    uid, ids = _seed(1)
    did = ids[0]
    with get_conn() as conn:
        for name, val, taken in [("Глюкоза", 5.4, "2026-05-02"), ("Глюкоза", 4.9, "2026-05-20"),
                                  ("Гемоглобин", 145, "2026-05-10")]:
            conn.execute(
                "INSERT INTO lab_results(document_id, user_id, analyte_name, value_num, taken_at) "
                "VALUES (?,?,?,?,?)", (did, uid, name, val, taken))
    groups = labs_in_period(uid, datetime(2026, 5, 1), datetime(2026, 5, 31))
    by_name = {g["analyte_name"]: g["points"] for g in groups}
    assert [p["value_num"] for p in by_name["Глюкоза"]] == [5.4, 4.9]  # по времени
    assert len(by_name["Гемоглобин"]) == 1
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_queries_ui.py -v`
Expected: FAIL (ImportError — функций нет)

- [ ] **Step 3: Реализовать запросы**

Добавить в `src/botkin/db/queries.py`:

```python
def get_document(document_id: int, user_id: int) -> dict | None:
    """Документ по id с проверкой принадлежности."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def get_document_status(document_id: int, user_id: int) -> str | None:
    """Текущий статус документа (для поллинга прогресса)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
    return row["status"] if row else None


def count_documents(user_id: int, doc_type: str | None = None) -> int:
    sql = "SELECT COUNT(*) AS c FROM documents WHERE user_id = ?"
    params: list = [user_id]
    if doc_type:
        sql += " AND doc_type = ?"
        params.append(doc_type)
    with get_conn() as conn:
        return conn.execute(sql, tuple(params)).fetchone()["c"]


def list_documents(user_id: int, doc_type: str | None = None,
                   limit: int = 7, offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM documents WHERE user_id = ?"
    params: list = [user_id]
    if doc_type:
        sql += " AND doc_type = ?"
        params.append(doc_type)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def documents_in_period(user_id: int, start, end, doc_type: str | None = None,
                        limit: int = 7, offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM documents WHERE user_id = ? AND created_at >= ? AND created_at <= ?"
    params: list = [user_id, str(start), str(end)]
    if doc_type:
        sql += " AND doc_type = ?"
        params.append(doc_type)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def labs_in_period(user_id: int, start, end) -> list[dict]:
    """Показатели за период, сгруппированные по analyte_name, точки по времени."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT analyte_name, value_num, unit, ref_low, ref_high, taken_at "
            "FROM lab_results WHERE user_id = ? AND taken_at >= ? AND taken_at <= ? "
            "AND value_num IS NOT NULL ORDER BY analyte_name ASC, taken_at ASC",
            (user_id, str(start), str(end)),
        ).fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        g = groups.setdefault(r["analyte_name"], {"analyte_name": r["analyte_name"], "points": []})
        g["points"].append(dict(r))
    return list(groups.values())
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_queries_ui.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/db/queries.py tests/test_queries_ui.py
git commit -m "feat(db): запросы для UI — документы, фильтр, период, сводка показателей"
```

---

## Фаза 2 — Извлечение метаданных (title + clinic)

### Task 4: `ClassifyResult`/`ClassifySchema` += `title`, `clinic`; промпт

**Files:**
- Modify: `src/botkin/domain/models.py:102-107` (`ClassifyResult`)
- Modify: `src/botkin/llm/prompts.py` (`CLASSIFY_VLM_SYSTEM`)
- Modify: `src/botkin/llm/classify.py:19-22, 58` (`ClassifySchema`, возврат)
- Test: `tests/test_classify_metadata.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_classify_metadata.py`:

```python
from botkin.domain.models import ClassifyResult


def test_classify_result_has_metadata_fields():
    r = ClassifyResult(doc_type="analysis", confidence=0.9,
                        title="Общий анализ мочи", clinic="Инвитро")
    assert r.title == "Общий анализ мочи"
    assert r.clinic == "Инвитро"


def test_classify_result_metadata_optional():
    r = ClassifyResult(doc_type="unknown", confidence=0.5)
    assert r.title is None
    assert r.clinic is None
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_classify_metadata.py -v`
Expected: FAIL (нет полей `title`/`clinic`)

- [ ] **Step 3: Реализовать**

В `src/botkin/domain/models.py`, класс `ClassifyResult`, добавить поля:

```python
class ClassifyResult(BaseModel):
    doc_type: DocType
    confidence: float = Field(..., ge=0.0, le=1.0)
    title: Optional[str] = None
    clinic: Optional[str] = None
```

(Убедиться, что `Optional` импортирован — он уже используется в файле.)

В `src/botkin/llm/prompts.py` заменить хвост `CLASSIFY_VLM_SYSTEM`:

```python
- unknown: не подходит ни под один из выше

Также извлеки:
- title: точное название документа как в источнике (например «Общий анализ мочи», «МРТ головного мозга», «Выписной эпикриз»). Если явного названия нет — null.
- clinic: название медучреждения/лаборатории (например «Инвитро», «Клиника Здоровье»). Если не видно — null.

Верни doc_type, confidence (0.0–1.0), title, clinic."""
```

В `src/botkin/llm/classify.py` расширить схему и возврат:

```python
class ClassifySchema(BaseModel):
    doc_type: DocType
    confidence: float
    title: str | None = None
    clinic: str | None = None
```

Строка 58 — возврат:

```python
        return ClassifyResult(
            doc_type=response.doc_type, confidence=response.confidence,
            title=response.title, clinic=response.clinic,
        )
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_classify_metadata.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/domain/models.py src/botkin/llm/prompts.py src/botkin/llm/classify.py tests/test_classify_metadata.py
git commit -m "feat(llm): classify извлекает title и clinic документа"
```

---

### Task 5: `DocumentRepo.set_metadata` + сохранение в orchestrator

**Files:**
- Modify: `src/botkin/db/repos.py` (`DocumentRepo`)
- Test: `tests/test_doc_metadata_repo.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_doc_metadata_repo.py`:

```python
from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, UserRepo


def test_set_metadata(set_test_db):
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(7)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/x.jpg")
        DocumentRepo(conn, uid).set_metadata(did, title="ОАК", clinic="Гемотест")
        row = conn.execute("SELECT title, clinic FROM documents WHERE id=?", (did,)).fetchone()
    assert row["title"] == "ОАК"
    assert row["clinic"] == "Гемотест"


def test_claim_delivery_atomic(set_test_db):
    """Первый захват возвращает True, повторный — False."""
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(8)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/y.jpg")
        assert DocumentRepo(conn, uid).claim_delivery(did) is True
        assert DocumentRepo(conn, uid).claim_delivery(did) is False
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_doc_metadata_repo.py -v`
Expected: FAIL (методов нет)

- [ ] **Step 3: Реализовать методы в `DocumentRepo`**

Добавить в класс `DocumentRepo` (`src/botkin/db/repos.py`):

```python
    def set_metadata(self, document_id: int, title: str | None, clinic: str | None) -> None:
        self.conn.execute(
            "UPDATE documents SET title = ?, clinic = ? WHERE id = ? AND user_id = ?",
            (title, clinic, document_id, self.user_id),
        )
        self.conn.commit()

    def claim_delivery(self, document_id: int) -> bool:
        """Атомарно помечает доставку; True если захватил первым."""
        cur = self.conn.execute(
            "UPDATE documents SET delivered_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND user_id = ? AND delivered_at IS NULL",
            (document_id, self.user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_doc_metadata_repo.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/db/repos.py tests/test_doc_metadata_repo.py
git commit -m "feat(db): DocumentRepo.set_metadata и атомарный claim_delivery"
```

---

### Task 6: Стадии статусов и сохранение метаданных в `orchestrator._run`

**Files:**
- Modify: `src/botkin/pipeline/orchestrator.py:57-115`
- Test: `tests/test_orchestrator_stages.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_orchestrator_stages.py`:

```python
import asyncio
from unittest.mock import patch
from botkin.domain.models import ClassifyResult, LabResult


async def _anoop(*a, **k):
    return None


def _make_doc():
    from botkin.db.connection import get_conn
    from botkin.db.repos import DocumentRepo, UserRepo
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(321)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/a.jpg")
    return uid, did


def test_title_clinic_saved_after_classify(set_test_db):
    from botkin.pipeline import orchestrator
    from botkin.db.connection import get_conn
    uid, did = _make_doc()
    with patch.object(orchestrator.classify, "run_vlm",
                      return_value=ClassifyResult(doc_type="analysis", confidence=0.9,
                                                  title="Биохимия", clinic="Инвитро")), \
         patch.object(orchestrator.extract, "run_analysis",
                      return_value=[LabResult(analyte_name="Глюкоза", value_num=5.0)]), \
         patch("botkin.pipeline.orchestrator.notify_user", side_effect=_anoop):
        asyncio.run(orchestrator.process_document(did, 321))
    with get_conn() as conn:
        row = conn.execute("SELECT title, clinic, status FROM documents WHERE id=?", (did,)).fetchone()
    assert row["title"] == "Биохимия"
    assert row["clinic"] == "Инвитро"
    assert row["status"] == "extracted"


def test_stages_recorded(set_test_db):
    """Стадии recognizing и normalizing проставляются по ходу."""
    from botkin.pipeline import orchestrator
    from botkin.db.connection import get_conn
    uid, did = _make_doc()
    seen = []

    def _spy_run_analysis(_path):
        with get_conn() as conn:
            seen.append(conn.execute("SELECT status FROM documents WHERE id=?", (did,)).fetchone()["status"])
        return [LabResult(analyte_name="Глюкоза", value_num=5.0)]

    with patch.object(orchestrator.classify, "run_vlm",
                      return_value=ClassifyResult(doc_type="analysis", confidence=0.9)), \
         patch.object(orchestrator.extract, "run_analysis", side_effect=_spy_run_analysis), \
         patch("botkin.pipeline.orchestrator.notify_user", side_effect=_anoop):
        asyncio.run(orchestrator.process_document(did, 321))
    # к моменту извлечения деталей статус уже normalizing (ставится перед extract)
    assert "normalizing" in seen
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_orchestrator_stages.py -v`
Expected: FAIL (`title` пустой / статус `processing`)

- [ ] **Step 3: Реализовать изменения в `_run`**

В `src/botkin/pipeline/orchestrator.py`:

Стр. 57-59 — заменить статус `processing` на `recognizing`:

```python
    # ── 1. Статус: распознавание ───────────────────────────────────────────
    with get_conn() as conn:
        DocumentRepo(conn, user_id).set_status(document_id, "recognizing")
```

Стр. 75-76 — после классификации сохранить doc_type И метаданные:

```python
    with get_conn() as conn:
        repo = DocumentRepo(conn, user_id)
        repo.set_doc_type(document_id, doc_type)
        repo.set_metadata(document_id, result.title, result.clinic)
```

Перед блоком извлечения (между стр. 77 и 78, перед `# ── 3. Extract`) добавить переключение стадии:

```python
    # ── Статус: нормализация (извлечение деталей + нормализация) ───────────
    with get_conn() as conn:
        DocumentRepo(conn, user_id).set_status(document_id, "normalizing")
```

Порядок в `_run`: `recognizing` (шаг 1) → `classify` → `set_doc_type` + `set_metadata` → `normalizing` → `extract` (спай видит `normalizing`) → `extracted` (финал). Тест `test_stages_recorded` уже написан под это (`assert "normalizing" in seen`).

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_orchestrator_stages.py tests/test_orchestrator.py -v`
Expected: PASS (включая существующий `test_orchestrator.py` — он проверяет финальный `extracted`, не промежуточные)

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/pipeline/orchestrator.py tests/test_orchestrator_stages.py
git commit -m "feat(pipeline): стадии recognizing/normalizing и сохранение title/clinic"
```

---

## Фаза 3 — Вывод ГРЛС и общий рендер карточки

### Task 7: ГРЛС-логика в форматировании рецептов

**Files:**
- Create: `src/botkin/bot/cards.py`
- Test: `tests/test_cards_rx.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_cards_rx.py`:

```python
from botkin.bot.cards import format_rx_line


def test_active_no_warning():
    line = format_rx_line({
        "drug_mnn": "Левокарнитин", "drug_trade": "Элькар", "dose": "300 мг/мл",
        "frequency": "утром", "duration_days": 30,
        "match_status": "matched", "reg_statuses": '["active","modified"]',
    })
    assert "Левокарнитин" in line and "Элькар" in line
    assert "⚠️" not in line and "❓" not in line


def test_no_active_warns():
    line = format_rx_line({
        "drug_mnn": "Фенитоин", "drug_trade": None, "dose": None,
        "frequency": None, "duration_days": None,
        "match_status": "matched", "reg_statuses": '["expired","suspended"]',
    })
    assert "⚠️" in line
    assert "нет действующих регистраций" in line


def test_unverified_flag_with_ratio():
    line = format_rx_line({
        "drug_mnn": "Левокарнитин", "drug_trade": "Элькар", "dose": None,
        "frequency": None, "duration_days": None,
        "match_status": "unverified", "reg_statuses": None, "ratio": 0.78,
    })
    assert "❓" in line and "78" in line


def test_missing_grls_fields_safe():
    line = format_rx_line({"drug_mnn": "Аспирин", "drug_trade": None, "dose": None,
                           "frequency": None, "duration_days": None})
    assert "Аспирин" in line and "⚠️" not in line
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_cards_rx.py -v`
Expected: FAIL (модуля нет)

- [ ] **Step 3: Реализовать `format_rx_line` в `cards.py`**

Создать `src/botkin/bot/cards.py`:

```python
"""Рендер карточек документов для Telegram (чистые функции, тестируемы без БД)."""
import html
import json

_PROBLEM_STATUSES = {"expired", "excluded", "suspended"}


def _reg_warning(reg_statuses_json: str | None) -> str:
    """⚠️ если в наборе статусов ГРЛС нет ни одного 'active'."""
    if not reg_statuses_json:
        return ""
    try:
        statuses = set(json.loads(reg_statuses_json))
    except (ValueError, TypeError):
        return ""
    if "active" in statuses:
        return ""
    if statuses & _PROBLEM_STATUSES:
        return "  ⚠️ нет действующих регистраций в РФ"
    return ""


def format_rx_line(r: dict) -> str:
    """Одна строка назначения с пометками ГРЛС."""
    mnn = html.escape(r["drug_mnn"])
    trade = f" ({html.escape(r['drug_trade'])})" if r.get("drug_trade") else ""
    dose = html.escape(r["dose"]) if r.get("dose") else ""
    freq = html.escape(r["frequency"]) if r.get("frequency") else ""
    dur = f", {r['duration_days']} дн." if r.get("duration_days") else ""

    flags = _reg_warning(r.get("reg_statuses"))
    if r.get("match_status") == "unverified":
        ratio = r.get("ratio")
        pct = f" ({round(ratio * 100)}%)" if isinstance(ratio, (int, float)) else ""
        flags += f"  ❓ распознано неточно{pct} — проверьте"

    parts = ", ".join(p for p in [dose, freq] if p)
    return f"• <b>{mnn}{trade}</b>: {parts}{dur}{flags}"
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_cards_rx.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/bot/cards.py tests/test_cards_rx.py
git commit -m "feat(bot): вывод ГРЛС-статуса и уверенности match в строке назначения"
```

---

### Task 8: Общий рендер карточки документа (шапка title/clinic) и рефактор `show.py`

**Files:**
- Modify: `src/botkin/bot/cards.py` (добавить `format_card`)
- Modify: `src/botkin/bot/handlers/show.py` (использовать `cards`)
- Modify: `src/botkin/db/queries.py` (`get_prescriptions` — добавить ГРЛС-поля)
- Test: `tests/test_cards_header.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_cards_header.py`:

```python
from botkin.bot.cards import format_card_header


def test_header_with_title_and_clinic():
    h = format_card_header({"id": 9, "doc_type": "analysis", "title": "Биохимия крови",
                            "clinic": "Инвитро", "created_at": "2026-05-28 14:30",
                            "status": "extracted"})
    assert "#9" in h and "Биохимия крови" in h and "Инвитро" in h


def test_header_fallback_title_from_doc_type():
    h = format_card_header({"id": 5, "doc_type": "prescription", "title": None,
                            "clinic": None, "created_at": "2026-05-01", "status": "extracted"})
    assert "Рецепт" in h        # лейбл из DOC_TYPE_LABELS
    assert "🏥 —" in h           # клиника не указана
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_cards_header.py -v`
Expected: FAIL (`format_card_header` нет)

- [ ] **Step 3: Реализовать `format_card_header` и расширить `get_prescriptions`**

Добавить в `src/botkin/bot/cards.py`:

```python
from botkin.domain.models import DOC_TYPE_LABELS

STATUS_EMOJI = {"received": "📥", "recognizing": "🔍", "normalizing": "🧩",
                "extracted": "✅", "failed": "❌"}
TYPE_EMOJI = {"analysis": "🧪", "prescription": "💊", "doctor_report": "👨‍⚕️",
              "certificate": "📄", "unknown": "📄"}


def doc_title(doc: dict) -> str:
    """Название документа: title, иначе лейбл типа."""
    if doc.get("title"):
        return html.escape(doc["title"])
    return DOC_TYPE_LABELS.get(doc.get("doc_type", "unknown"), "Документ 📄")


def format_card_header(doc: dict) -> str:
    status = STATUS_EMOJI.get(doc.get("status"), "❓")
    type_e = TYPE_EMOJI.get(doc.get("doc_type"), "📄")
    clinic = html.escape(doc["clinic"]) if doc.get("clinic") else "—"
    return (
        f"{status} Документ #{doc['id']} · {type_e} {doc_title(doc)}\n"
        f"🏥 {clinic} · {doc.get('created_at', '')}"
    )
```

В `src/botkin/db/queries.py`, функция `get_prescriptions` — добавить ГРЛС-поля в SELECT:

```python
def get_prescriptions(document_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT drug_mnn, drug_trade, dose, frequency, duration_days, "
            "match_status, reg_statuses FROM prescriptions WHERE document_id = ?",
            (document_id,),
        ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_cards_header.py -v`
Expected: PASS

- [ ] **Step 5: Перенаправить `show.py` на общий рендер и прогнать смоук**

В `src/botkin/bot/handlers/show.py`:
- удалить локальные `STATUS_EMOJI`, `TYPE_EMOJI` и `_format_rx`;
- импортировать `from botkin.bot.cards import format_card_header, format_rx_line`;
- в `cmd_show` заменить ручную сборку шапки на `format_card_header(doc)`;
- в `_format_rx` (внутри `_format_document`) использовать `format_rx_line(r)` для каждой строки.

Run: `uv run pytest tests/test_smoke.py tests/test_cards_rx.py tests/test_cards_header.py -v`
Expected: PASS

- [ ] **Step 6: Коммит**

```bash
git add src/botkin/bot/cards.py src/botkin/bot/handlers/show.py src/botkin/db/queries.py tests/test_cards_header.py
git commit -m "feat(bot): общий рендер карточки (шапка title/clinic), рефактор show.py"
```

---

## Фаза 4 — Прогресс-бар, поллинг, push-fallback

### Task 9: `render_progress(status)` — чистая функция

**Files:**
- Create: `src/botkin/bot/progress.py`
- Test: `tests/test_progress_render.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_progress_render.py`:

```python
from botkin.bot.progress import render_progress, is_terminal


def test_render_marks_current_stage():
    text = render_progress("recognizing", doc_id=9)
    assert "#9" in text
    assert "📥 Принято ✓" in text
    assert "🔍 Распознаю текст ●" in text
    assert "🧩 Нормализую данные" in text and "🧩 Нормализую данные ●" not in text


def test_render_normalizing():
    text = render_progress("normalizing", doc_id=1)
    assert "🔍 Распознаю текст ✓" in text
    assert "🧩 Нормализую данные ●" in text


def test_is_terminal():
    assert is_terminal("extracted") is True
    assert is_terminal("failed") is True
    assert is_terminal("recognizing") is False
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_progress_render.py -v`
Expected: FAIL (модуля нет)

- [ ] **Step 3: Реализовать**

Создать `src/botkin/bot/progress.py`:

```python
"""Прогресс-бар обработки документа: рендер стадий + поллинг статуса."""

_STAGES = [
    ("received", "📥 Принято"),
    ("recognizing", "🔍 Распознаю текст"),
    ("normalizing", "🧩 Нормализую данные"),
    ("extracted", "✅ Готово"),
]
_ORDER = {name: i for i, (name, _) in enumerate(_STAGES)}

TERMINAL = {"extracted", "failed"}


def is_terminal(status: str | None) -> bool:
    return status in TERMINAL


def render_progress(status: str, doc_id: int) -> str:
    """Текст прогресс-бара: пройденные — ✓, текущая — ●, будущие — без маркера."""
    cur = _ORDER.get(status, 0)
    lines = [f"⏳ Документ #{doc_id} — обрабатываю"]
    for i, (_, label) in enumerate(_STAGES):
        if i < cur:
            lines.append(f"{label} ✓")
        elif i == cur:
            lines.append(f"{label} ●")
        else:
            lines.append(label)
    return "\n".join(lines)
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_progress_render.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/bot/progress.py tests/test_progress_render.py
git commit -m "feat(bot): render_progress — прогресс-бар стадий обработки"
```

---

### Task 10: Поллинг-цикл (тестируемый, без реального sleep)

**Files:**
- Modify: `src/botkin/bot/progress.py` (добавить `poll_until_done`)
- Test: `tests/test_progress_poll.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_progress_poll.py`:

```python
import asyncio
from botkin.bot.progress import poll_until_done


def test_poll_edits_only_on_change_and_returns_final():
    statuses = iter(["recognizing", "recognizing", "normalizing", "extracted"])
    edits = []

    async def fake_status():
        return next(statuses)

    async def fake_edit(text):
        edits.append(text)

    async def fast_sleep(_):
        return None

    final = asyncio.run(poll_until_done(
        doc_id=9, get_status=fake_status, edit=fake_edit,
        sleep=fast_sleep, interval=0.0, timeout=100.0, now=_clock([0, 1, 2, 3, 4]),
    ))
    assert final == "extracted"
    # редактируем на смене НЕтерминальных стадий: recognizing, normalizing
    # (терминальный extracted не рисуем — финальную карточку рисует вызывающий код)
    assert len(edits) == 2


def test_poll_timeout_returns_none():
    async def fake_status():
        return "recognizing"

    async def fake_edit(text):
        return None

    async def fast_sleep(_):
        return None

    final = asyncio.run(poll_until_done(
        doc_id=1, get_status=fake_status, edit=fake_edit,
        sleep=fast_sleep, interval=0.0, timeout=5.0, now=_clock([0, 2, 4, 6]),
    ))
    assert final is None


def _clock(values):
    it = iter(values)
    return lambda: next(it)
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_progress_poll.py -v`
Expected: FAIL (`poll_until_done` нет)

- [ ] **Step 3: Реализовать `poll_until_done`**

Добавить в `src/botkin/bot/progress.py`:

```python
async def poll_until_done(doc_id, get_status, edit, sleep, now,
                          interval: float = 2.0, timeout: float = 120.0):
    """Поллит статус, редактирует сообщение при смене стадии.

    Параметры-функции инъектируются для тестируемости:
      get_status() -> awaitable[str|None]; edit(text)->awaitable;
      sleep(sec)->awaitable; now()->float (монотонные секунды).
    Возвращает финальный статус (extracted/failed) или None при таймауте.
    """
    start = now()
    last_rendered = None
    while now() - start <= timeout:
        status = await get_status()
        if status and status != last_rendered:
            if is_terminal(status):
                return status
            await edit(render_progress(status, doc_id))
            last_rendered = status
        await sleep(interval)
    return None
```

(Примечание: при достижении терминального статуса функция возвращает его БЕЗ редактирования прогресс-бара — финальную карточку рисует вызывающий код, см. Task 12.)

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_progress_poll.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/bot/progress.py tests/test_progress_poll.py
git commit -m "feat(bot): poll_until_done — поллинг статуса с редактированием по смене стадии"
```

---

### Task 11: Push-fallback в orchestrator (отложенный захват доставки)

**Files:**
- Modify: `src/botkin/pipeline/orchestrator.py` (финальный блок)
- Modify: `src/botkin/pipeline/notifications.py` (формат финальной карточки)
- Modify: `src/botkin/config.py` (константы таймаута)
- Test: `tests/test_orchestrator_delivery.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_orchestrator_delivery.py`:

```python
import asyncio
from unittest.mock import patch
from botkin.domain.models import ClassifyResult, LabResult


def _make_doc():
    from botkin.db.connection import get_conn
    from botkin.db.repos import DocumentRepo, UserRepo
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(999)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/a.jpg")
    return uid, did


def test_fallback_sends_when_bot_did_not_deliver(set_test_db, monkeypatch):
    from botkin.pipeline import orchestrator
    monkeypatch.setattr(orchestrator, "DELIVERY_FALLBACK_DELAY", 0.0)
    uid, did = _make_doc()
    sent = []

    async def spy_notify(tg_id, text):
        sent.append(text)

    with patch.object(orchestrator.classify, "run_vlm",
                      return_value=ClassifyResult(doc_type="analysis", confidence=0.9)), \
         patch.object(orchestrator.extract, "run_analysis",
                      return_value=[LabResult(analyte_name="Глюкоза", value_num=5.0)]), \
         patch("botkin.pipeline.orchestrator.notify_user", side_effect=spy_notify):
        asyncio.run(orchestrator.process_document(did, 999))
    assert len(sent) == 1   # бот не доставил → fallback отправил


def test_fallback_silent_when_bot_delivered(set_test_db, monkeypatch):
    from botkin.pipeline import orchestrator
    from botkin.db.connection import get_conn
    from botkin.db.repos import DocumentRepo
    monkeypatch.setattr(orchestrator, "DELIVERY_FALLBACK_DELAY", 0.0)
    uid, did = _make_doc()
    sent = []

    async def spy_notify(tg_id, text):
        sent.append(text)

    def deliver_during_extract(_path):
        with get_conn() as conn:
            DocumentRepo(conn, uid).claim_delivery(did)  # имитируем доставку ботом
        return [LabResult(analyte_name="Глюкоза", value_num=5.0)]

    with patch.object(orchestrator.classify, "run_vlm",
                      return_value=ClassifyResult(doc_type="analysis", confidence=0.9)), \
         patch.object(orchestrator.extract, "run_analysis", side_effect=deliver_during_extract), \
         patch("botkin.pipeline.orchestrator.notify_user", side_effect=spy_notify):
        asyncio.run(orchestrator.process_document(did, 999))
    assert sent == []   # бот уже доставил → fallback молчит
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_orchestrator_delivery.py -v`
Expected: FAIL (нет `DELIVERY_FALLBACK_DELAY`; финал всегда шлёт notify)

- [ ] **Step 3: Реализовать fallback**

В `src/botkin/config.py` добавить (рядом с прочими константами):

```python
DELIVERY_FALLBACK_DELAY = float(os.getenv("DELIVERY_FALLBACK_DELAY", "130"))
```

(Убедиться, что `import os` присутствует — он используется в файле.)

В `src/botkin/pipeline/orchestrator.py`:
- вверху добавить `import asyncio` (уже есть) и `from botkin.config import DELIVERY_FALLBACK_DELAY` (или прочитать через существующий импорт config). Добавить модульную константу-ссылку для патча в тесте:

```python
from botkin.config import DELIVERY_FALLBACK_DELAY
```

Заменить финальный блок (стр. 110-115) на:

```python
    # ── 4. Финал ───────────────────────────────────────────────────────────
    with get_conn() as conn:
        DocumentRepo(conn, user_id).set_status(document_id, "extracted")
    log.info("Doc %d processed", document_id)

    # Push-fallback: ждём, пока поллинг бота покажет результат и захватит доставку.
    await asyncio.sleep(DELIVERY_FALLBACK_DELAY)
    with get_conn() as conn:
        claimed = DocumentRepo(conn, user_id).claim_delivery(document_id)
    if claimed:
        await notify_user(telegram_user_id, document_processed(document_id, doc_type))
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_orchestrator_delivery.py tests/test_orchestrator.py -v`
Expected: PASS

(Существующий `test_orchestrator.py` мокает `notify_user` и не проверяет число вызовов — с `DELIVERY_FALLBACK_DELAY` по умолчанию 130 он бы ждал; для скорости тестов он не трогает константу, но `process_document` досыпает. Если существующий тест станет медленным — добавить в него `monkeypatch.setattr(orchestrator, "DELIVERY_FALLBACK_DELAY", 0.0)`.)

**Доп. шаг:** обновить `tests/test_orchestrator.py` — добавить в начало теста `import` и `monkeypatch.setattr` для скорости:

```python
def test_prescription_drug_normalized_and_raw_saved(set_test_db, monkeypatch):
    from botkin.pipeline import orchestrator
    monkeypatch.setattr(orchestrator, "DELIVERY_FALLBACK_DELAY", 0.0)
    ...
```

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/pipeline/orchestrator.py src/botkin/config.py tests/test_orchestrator_delivery.py tests/test_orchestrator.py
git commit -m "feat(pipeline): push-fallback доставки финала через атомарный claim_delivery"
```

---

### Task 12: Интеграция поллинга в `upload.py`

**Files:**
- Modify: `src/botkin/bot/handlers/upload.py`
- Test: `tests/test_upload_progress.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_upload_progress.py` (тестируем выделенную корутину запуска прогресса, без реальной сети):

```python
import asyncio
from unittest.mock import AsyncMock
from botkin.bot.handlers.upload import run_progress_flow


def test_run_progress_flow_shows_card_on_success(monkeypatch):
    import botkin.bot.handlers.upload as up

    async def fake_poll(**kwargs):
        return "extracted"

    monkeypatch.setattr(up, "poll_until_done", fake_poll)
    monkeypatch.setattr(up, "get_user_id", lambda tg: 1)
    monkeypatch.setattr(up, "render_document_card", lambda doc_id, uid: "КАРТОЧКА #9")

    edit = AsyncMock()
    delivered = {"flag": False}
    monkeypatch.setattr(up, "claim_delivery_for", lambda doc_id, uid: delivered.__setitem__("flag", True) or True)

    asyncio.run(run_progress_flow(tg_user_id=10, doc_id=9, edit=edit))
    edit.assert_awaited()                       # финал отрисован
    assert "КАРТОЧКА #9" in edit.await_args.args[0]
    assert delivered["flag"] is True            # доставка захвачена


def test_run_progress_flow_timeout(monkeypatch):
    import botkin.bot.handlers.upload as up

    async def fake_poll(**kwargs):
        return None

    monkeypatch.setattr(up, "poll_until_done", fake_poll)
    monkeypatch.setattr(up, "get_user_id", lambda tg: 1)
    edit = AsyncMock()
    asyncio.run(run_progress_flow(tg_user_id=10, doc_id=9, edit=edit))
    assert "затянул" in edit.await_args.args[0].lower()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_upload_progress.py -v`
Expected: FAIL (`run_progress_flow` нет)

- [ ] **Step 3: Реализовать обвязку в `upload.py`**

В `src/botkin/bot/handlers/upload.py` добавить импорты и функции:

```python
import asyncio
import time

from botkin.bot.progress import poll_until_done, is_terminal
from botkin.bot.cards import format_card_header
from botkin.db.queries import get_user_id, get_document, get_document_status, get_prescriptions
from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo


def render_document_card(doc_id: int, user_id: int) -> str:
    """Полная карточка документа по id (шапка + детали из show-рендера)."""
    from botkin.bot.handlers.show import _format_document
    doc = get_document(doc_id, user_id)
    if not doc:
        return "❌ Документ не найден."
    return f"{format_card_header(doc)}\n────────────\n{_format_document(doc_id, doc)}"


def claim_delivery_for(doc_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        return DocumentRepo(conn, user_id).claim_delivery(doc_id)


async def run_progress_flow(tg_user_id: int, doc_id: int, edit) -> None:
    """Поллит статус и по завершении показывает карточку. `edit(text)` — корутина."""
    user_id = get_user_id(tg_user_id)
    if not user_id:
        return

    async def _get_status():
        return get_document_status(doc_id, user_id)

    final = await poll_until_done(
        doc_id=doc_id, get_status=_get_status, edit=edit,
        sleep=asyncio.sleep, now=time.monotonic,
    )
    if final == "extracted":
        if claim_delivery_for(doc_id, user_id):
            await edit(render_document_card(doc_id, user_id))
    elif final == "failed":
        await edit(f"❌ Документ #{doc_id}: обработка завершилась ошибкой.")
    else:
        await edit("⏳ Обработка затянулась. Загляните позже через /show.")
```

Затем в `on_photo` и `on_document` — после успешного `_upload_to_api`, заменить пару статических сообщений на запуск живого прогресса:

```python
        result = await _upload_to_api(message.from_user.id, filename, file_bytes.read())
        doc_id = result["document_id"]
        sent = await message.answer(render_progress("received", doc_id))

        async def _edit(text: str):
            try:
                await sent.edit_text(text)
            except Exception as e:  # noqa: BLE001 — "message is not modified" и пр.
                log.debug("edit skipped: %s", e)

        asyncio.create_task(run_progress_flow(message.from_user.id, doc_id, _edit))
        await message.answer(photo_followup_text(photo.width))  # только в on_photo
```

(Добавить `from botkin.bot.progress import render_progress`. В `on_document` подсказку про разрешение не слать.)

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_upload_progress.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/bot/handlers/upload.py tests/test_upload_progress.py
git commit -m "feat(bot): живой прогресс-бар обработки после загрузки + авто-карточка"
```

---

## Фаза 5 — Навигация (keyboards + browse)

### Task 13: `keyboards.py` — кодирование `callback_data` и клавиатуры

**Files:**
- Create: `src/botkin/bot/keyboards.py`
- Test: `tests/test_keyboards.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_keyboards.py`:

```python
from botkin.bot.keyboards import encode_cb, decode_cb, TYPE_CODES


def test_roundtrip_doc():
    assert decode_cb(encode_cb("doc", 22)) == ("doc", ["22"])


def test_roundtrip_list():
    cb = encode_cb("lst", "a", 7)
    assert decode_cb(cb) == ("lst", ["a", "7"])


def test_roundtrip_nav():
    assert decode_cb(encode_cb("nav", 5, "next")) == ("nav", ["5", "next"])


def test_under_64_bytes():
    cb = encode_cb("per", "month", "labs")
    assert len(cb.encode("utf-8")) <= 64


def test_type_codes_map_all():
    assert set(TYPE_CODES.values()) >= {"analysis", "prescription", "doctor_report"}
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_keyboards.py -v`
Expected: FAIL (модуля нет)

- [ ] **Step 3: Реализовать кодирование**

Создать `src/botkin/bot/keyboards.py`:

```python
"""Inline-клавиатуры и компактное кодирование callback_data (лимит 64 байта)."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

_SEP = ":"

# Коды типов для краткого callback_data.
TYPE_CODES = {"a": "analysis", "p": "prescription", "d": "doctor_report", "all": None}
CODE_BY_TYPE = {"analysis": "a", "prescription": "p", "doctor_report": "d", None: "all"}


def encode_cb(action: str, *parts) -> str:
    return _SEP.join([action, *[str(p) for p in parts]])


def decode_cb(data: str) -> tuple[str, list[str]]:
    action, *parts = data.split(_SEP)
    return action, parts
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_keyboards.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/bot/keyboards.py tests/test_keyboards.py
git commit -m "feat(bot): кодирование callback_data и коды типов документов"
```

---

### Task 14: Клавиатуры списка и карточки

**Files:**
- Modify: `src/botkin/bot/keyboards.py`
- Test: `tests/test_keyboards_build.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_keyboards_build.py`:

```python
from botkin.bot.keyboards import list_keyboard, card_keyboard


def _texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def _datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_list_keyboard_has_filters_numbers_and_paging():
    ids = [11, 12, 13]
    kb = list_keyboard(ids, doc_type=None, offset=0, total=20)
    texts = _texts(kb)
    assert "🧪" in texts and "💊" in texts and "👨‍⚕️" in texts and "Все" in texts
    assert "1" in texts and "3" in texts          # номера по количеству на странице
    assert any("Вперёд" in t for t in texts)      # есть следующая страница
    assert not any("Назад" in t for t in texts)   # на offset=0 назад нет


def test_card_keyboard_nav():
    kb = card_keyboard(doc_id=12, has_prev=True, has_next=False)
    datas = _datas(kb)
    assert "nav:12:prev" in datas
    assert "lst:all:0" in datas                   # кнопка «к списку»
    assert "nav:12:next" not in datas             # нет следующего
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_keyboards_build.py -v`
Expected: FAIL (функций нет)

- [ ] **Step 3: Реализовать сборку клавиатур**

Добавить в `src/botkin/bot/keyboards.py`:

```python
PAGE_SIZE = 7
_FILTERS = [("🧪", "a"), ("💊", "p"), ("👨‍⚕️", "d"), ("Все", "all")]


def list_keyboard(doc_ids: list[int], doc_type, offset: int, total: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    # ряд фильтров
    for label, code in _FILTERS:
        b.button(text=label, callback_data=encode_cb("lst", code, 0))
    # ряд номеров выбора
    for i, did in enumerate(doc_ids, start=1):
        b.button(text=str(i), callback_data=encode_cb("doc", did))
    # ряд пагинации
    code = CODE_BY_TYPE.get(doc_type, "all")
    nav_row = []
    if offset > 0:
        nav_row.append(InlineKeyboardButton(
            text="← Назад", callback_data=encode_cb("lst", code, max(0, offset - PAGE_SIZE))))
    if offset + PAGE_SIZE < total:
        nav_row.append(InlineKeyboardButton(
            text="Вперёд →", callback_data=encode_cb("lst", code, offset + PAGE_SIZE)))
    b.adjust(len(_FILTERS), len(doc_ids))
    kb = b.as_markup()
    if nav_row:
        kb.inline_keyboard.append(nav_row)
    return kb


def card_keyboard(doc_id: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    row = []
    if has_prev:
        row.append(InlineKeyboardButton(text="← Пред.", callback_data=encode_cb("nav", doc_id, "prev")))
    row.append(InlineKeyboardButton(text="☰ К списку", callback_data=encode_cb("lst", "all", 0)))
    if has_next:
        row.append(InlineKeyboardButton(text="След. →", callback_data=encode_cb("nav", doc_id, "next")))
    return InlineKeyboardMarkup(inline_keyboard=[row])
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_keyboards_build.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/bot/keyboards.py tests/test_keyboards_build.py
git commit -m "feat(bot): клавиатуры списка (фильтры/номера/пагинация) и карточки (навигация)"
```

---

### Task 15: Хендлеры `/list` и callback-роутинг (список + карточка)

**Files:**
- Create: `src/botkin/bot/handlers/browse.py`
- Modify: `src/botkin/bot/cards.py` (рендер тела списка)
- Test: `tests/test_browse.py` (создать)

- [ ] **Step 1: Написать падающий тест (рендер тела списка — чистая функция)**

Создать `tests/test_browse.py`:

```python
from botkin.bot.cards import format_list_body


def test_format_list_body_numbered_with_title_clinic():
    docs = [
        {"id": 23, "doc_type": "doctor_report", "title": "Заключение невролога",
         "clinic": "Клиника Здоровье", "created_at": "2026-06-02 10:00"},
        {"id": 22, "doc_type": "analysis", "title": None,
         "clinic": None, "created_at": "2026-05-28 14:30"},
    ]
    body = format_list_body(docs, offset=0, total=2)
    assert "1." in body and "2." in body
    assert "Заключение невролога" in body
    assert "Анализы" in body         # fallback названия из лейбла типа
    assert "🏥 —" in body            # клиника не указана
    assert "1–2 из 2" in body
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_browse.py -v`
Expected: FAIL

- [ ] **Step 3: Реализовать `format_list_body` и хендлеры**

Добавить в `src/botkin/bot/cards.py`:

```python
def format_list_body(docs: list[dict], offset: int, total: int) -> str:
    if not docs:
        return "📭 Документов пока нет."
    head = f"📁 Твои документы ({offset + 1}–{offset + len(docs)} из {total})\n"
    lines = [head]
    for i, d in enumerate(docs, start=1):
        type_e = TYPE_EMOJI.get(d.get("doc_type"), "📄")
        clinic = html.escape(d["clinic"]) if d.get("clinic") else "—"
        date = str(d.get("created_at", ""))[:10]
        lines.append(f"{i}. {type_e} {doc_title(d)}\n   🏥 {clinic} · {date}")
    return "\n".join(lines)
```

Создать `src/botkin/bot/handlers/browse.py`:

```python
"""Навигация по документам: /list, карточка, листание, фильтр."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from botkin.bot.cards import format_card_header, format_list_body
from botkin.bot.keyboards import (
    CODE_BY_TYPE, PAGE_SIZE, TYPE_CODES, card_keyboard, decode_cb, list_keyboard,
)
from botkin.db.queries import (
    count_documents, get_document, get_user_id, list_documents,
)

router = Router(name="browse")


async def _need_user(obj, tg_id: int) -> int | None:
    uid = get_user_id(tg_id)
    if not uid:
        await (obj.answer if isinstance(obj, Message) else obj.message.answer)(
            "⚠️ Отправь /start для регистрации.")
    return uid


def _render_card(doc_id: int, user_id: int):
    from botkin.bot.handlers.show import _format_document
    doc = get_document(doc_id, user_id)
    if not doc:
        return None, None
    # соседи по дате в пределах всех документов пользователя
    siblings = [d["id"] for d in list_documents(user_id, limit=10_000)]
    idx = siblings.index(doc_id) if doc_id in siblings else 0
    has_prev = idx < len(siblings) - 1     # список по убыванию даты → next = старее
    has_next = idx > 0
    text = f"{format_card_header(doc)}\n────────────\n{_format_document(doc_id, doc)}"
    return text, card_keyboard(doc_id, has_prev=has_prev, has_next=has_next)


async def _show_list(target, user_id: int, code: str, offset: int):
    doc_type = TYPE_CODES.get(code)
    total = count_documents(user_id, doc_type=doc_type)
    docs = list_documents(user_id, doc_type=doc_type, limit=PAGE_SIZE, offset=offset)
    body = format_list_body(docs, offset=offset, total=total)
    kb = list_keyboard([d["id"] for d in docs], doc_type=doc_type, offset=offset, total=total)
    await target(body, reply_markup=kb)


@router.message(Command("list"))
async def cmd_list(message: Message) -> None:
    uid = await _need_user(message, message.from_user.id)
    if not uid:
        return
    await _show_list(message.answer, uid, "all", 0)


@router.callback_query()
async def on_callback(cb: CallbackQuery) -> None:
    uid = await _need_user(cb, cb.from_user.id)
    if not uid:
        await cb.answer()
        return
    action, parts = decode_cb(cb.data)

    if action == "lst":
        code, offset = parts[0], int(parts[1])
        await _show_list(cb.message.edit_text, uid, code, offset)

    elif action == "doc":
        text, kb = _render_card(int(parts[0]), uid)
        if text is None:
            await cb.answer("Документ не найден", show_alert=True)
        else:
            await cb.message.edit_text(text, reply_markup=kb)

    elif action == "nav":
        doc_id, direction = int(parts[0]), parts[1]
        siblings = [d["id"] for d in list_documents(uid, limit=10_000)]
        if doc_id in siblings:
            i = siblings.index(doc_id)
            j = i + 1 if direction == "prev" else i - 1   # prev = старее (дальше по списку)
            if 0 <= j < len(siblings):
                text, kb = _render_card(siblings[j], uid)
                await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_browse.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/bot/handlers/browse.py src/botkin/bot/cards.py tests/test_browse.py
git commit -m "feat(bot): /list, карточка документа, листание и фильтр по типу"
```

---

### Task 16: Кнопки навигации под `/show`

**Files:**
- Modify: `src/botkin/bot/handlers/show.py`
- Test: расширить `tests/test_smoke.py` (проверка импорта клавиатуры)

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_smoke.py`:

```python
def test_show_attaches_nav_keyboard():
    from botkin.bot.keyboards import card_keyboard
    kb = card_keyboard(doc_id=1, has_prev=False, has_next=False)
    # хотя бы кнопка «к списку» всегда присутствует
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(d.startswith("lst:") for d in datas)
```

- [ ] **Step 2: Запустить — убедиться, что проходит/падает**

Run: `uv run pytest tests/test_smoke.py::test_show_attaches_nav_keyboard -v`
Expected: PASS (функция уже есть из Task 14 — этот тест фиксирует контракт)

- [ ] **Step 3: Прикрепить клавиатуру в `cmd_show`**

В `src/botkin/bot/handlers/show.py`, в конце `cmd_show`, заменить вызов `message.answer(...)` на вариант с клавиатурой:

```python
    from botkin.bot.keyboards import card_keyboard
    await message.answer(
        f"{format_card_header(doc)}\n────────────\n{details}",
        reply_markup=card_keyboard(doc["id"], has_prev=False, has_next=False),
    )
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/bot/handlers/show.py tests/test_smoke.py
git commit -m "feat(bot): кнопки навигации под /show"
```

---

## Фаза 6 — Запросы за период

### Task 17: Парсинг периода (пресеты + ручной ввод)

**Files:**
- Create: `src/botkin/bot/period.py`
- Test: `tests/test_period.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_period.py`:

```python
from datetime import datetime
from botkin.bot.period import preset_range, parse_manual


def test_preset_month():
    start, end = preset_range("month", now=datetime(2026, 6, 15, 12, 0))
    assert start == datetime(2026, 5, 15, 12, 0)
    assert end == datetime(2026, 6, 15, 12, 0)


def test_preset_all():
    start, end = preset_range("all", now=datetime(2026, 6, 15))
    assert start.year <= 1970 or start == datetime(1970, 1, 1)
    assert end == datetime(2026, 6, 15)


def test_parse_manual_months():
    start, end = parse_manual(["2026-01", "2026-03"])
    assert start == datetime(2026, 1, 1, 0, 0, 0)
    assert end == datetime(2026, 3, 31, 23, 59, 59)


def test_parse_manual_days():
    start, end = parse_manual(["2026-01-10", "2026-01-20"])
    assert start == datetime(2026, 1, 10, 0, 0, 0)
    assert end == datetime(2026, 1, 20, 23, 59, 59)


def test_parse_manual_invalid():
    assert parse_manual(["мусор"]) is None
    assert parse_manual([]) is None
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_period.py -v`
Expected: FAIL (модуля нет)

- [ ] **Step 3: Реализовать**

Создать `src/botkin/bot/period.py`:

```python
"""Парсинг периода: пресеты и ручной ввод дат."""
import calendar
from datetime import datetime


def preset_range(preset: str, now: datetime) -> tuple[datetime, datetime]:
    if preset == "month":
        start = now.replace(month=now.month - 1) if now.month > 1 else now.replace(year=now.year - 1, month=12)
    elif preset == "3m":
        m = now.month - 3
        start = now.replace(year=now.year + (m - 1) // 12, month=(m - 1) % 12 + 1)
    elif preset == "year":
        start = now.replace(year=now.year - 1)
    else:  # all
        start = datetime(1970, 1, 1)
    return start, now


def _end_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def parse_manual(args: list[str]) -> tuple[datetime, datetime] | None:
    """Принимает ['YYYY-MM','YYYY-MM'] или ['YYYY-MM-DD','YYYY-MM-DD']."""
    if len(args) != 2:
        return None
    try:
        a, b = args
        if len(a) == 7:  # YYYY-MM
            sy, sm = map(int, a.split("-"))
            ey, em = map(int, b.split("-"))
            start = datetime(sy, sm, 1, 0, 0, 0)
            end = datetime(ey, em, _end_of_month(ey, em), 23, 59, 59)
        else:            # YYYY-MM-DD
            start = datetime.strptime(a, "%Y-%m-%d").replace(hour=0, minute=0, second=0)
            end = datetime.strptime(b, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        return start, end
    except (ValueError, TypeError):
        return None
```

(Примечание: `preset_range("month")` вычитает один месяц от `now`; тест ожидает именно сдвиг на месяц назад от текущего момента.)

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_period.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/bot/period.py tests/test_period.py
git commit -m "feat(bot): парсинг периода — пресеты и ручной ввод дат"
```

---

### Task 18: Сводка показателей за период (рендер)

**Files:**
- Modify: `src/botkin/bot/cards.py`
- Test: `tests/test_period_summary.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_period_summary.py`:

```python
from botkin.bot.cards import format_labs_summary


def test_summary_shows_trend_and_norm():
    groups = [
        {"analyte_name": "Глюкоза", "points": [
            {"value_num": 5.4, "unit": "ммоль/л", "ref_low": 3.9, "ref_high": 6.1},
            {"value_num": 4.9, "unit": "ммоль/л", "ref_low": 3.9, "ref_high": 6.1}]},
        {"analyte_name": "Холестерин", "points": [
            {"value_num": 6.8, "unit": "ммоль/л", "ref_low": None, "ref_high": 5.2}]},
    ]
    text = format_labs_summary(groups, label="3 месяца")
    assert "Глюкоза" in text and "5.4" in text and "4.9" in text     # тренд первое→последнее
    assert "Холестерин" in text and "⬆️" in text                     # выше нормы
    assert "3 месяца" in text


def test_summary_empty():
    assert "нет" in format_labs_summary([], label="месяц").lower()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_period_summary.py -v`
Expected: FAIL

- [ ] **Step 3: Реализовать `format_labs_summary`**

Добавить в `src/botkin/bot/cards.py`:

```python
def format_labs_summary(groups: list[dict], label: str) -> str:
    if not groups:
        return f"📊 За {label}: данных по показателям нет."
    total = sum(len(g["points"]) for g in groups)
    lines = [f"📊 Показатели за {label} (по {total} значениям)", "────────────"]
    for g in groups:
        pts = g["points"]
        vals = [p["value_num"] for p in pts]
        unit = html.escape(pts[-1].get("unit") or "")
        trend = " → ".join(str(v) for v in vals)
        last = pts[-1]
        marker = ""
        lo, hi, v = last.get("ref_low"), last.get("ref_high"), last["value_num"]
        if hi is not None and v > hi:
            marker = " ⬆️"
        elif lo is not None and v < lo:
            marker = " ⬇️"
        norm = ""
        if lo is not None and hi is not None:
            norm = f"  (норма {lo}–{hi})"
        elif hi is not None:
            norm = f"  (норма <{hi})"
        name = html.escape(g["analyte_name"])
        lines.append(f"{name}: {trend} {unit}{marker}{norm}")
    return "\n".join(lines)
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_period_summary.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/bot/cards.py tests/test_period_summary.py
git commit -m "feat(bot): сводка показателей за период с трендом и нормой"
```

---

### Task 19: Хендлер `/period` и его callback-роутинг

**Files:**
- Modify: `src/botkin/bot/handlers/browse.py`
- Modify: `src/botkin/bot/keyboards.py` (клавиатуры периода)
- Test: `tests/test_period_keyboards.py` (создать)

- [ ] **Step 1: Написать падающий тест (клавиатуры периода — чистые)**

Создать `tests/test_period_keyboards.py`:

```python
from botkin.bot.keyboards import period_presets_keyboard, period_view_keyboard


def _datas(m):
    return [b.callback_data for row in m.inline_keyboard for b in row]


def test_presets_keyboard():
    kb = period_presets_keyboard()
    datas = _datas(kb)
    assert "per:month:menu" in datas and "per:all:menu" in datas


def test_view_keyboard():
    kb = period_view_keyboard("month")
    datas = _datas(kb)
    assert "per:month:docs" in datas and "per:month:labs" in datas
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_period_keyboards.py -v`
Expected: FAIL

- [ ] **Step 3: Реализовать клавиатуры периода и хендлеры**

Добавить в `src/botkin/bot/keyboards.py`:

```python
_PRESETS = [("Месяц", "month"), ("3 месяца", "3m"), ("Год", "year"), ("Всё время", "all")]


def period_presets_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for label, code in _PRESETS:
        b.button(text=label, callback_data=encode_cb("per", code, "menu"))
    b.adjust(2, 2)
    return b.as_markup()


def period_view_keyboard(preset: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📁 Документы", callback_data=encode_cb("per", preset, "docs"))
    b.button(text="📊 Показатели", callback_data=encode_cb("per", preset, "labs"))
    return b.as_markup()
```

В `src/botkin/bot/handlers/browse.py`:
- импортировать `from botkin.bot.period import preset_range, parse_manual`, `from botkin.bot.cards import format_labs_summary`, `from botkin.bot.keyboards import period_presets_keyboard, period_view_keyboard`, `from botkin.db.queries import documents_in_period, labs_in_period`, `from datetime import datetime`, `from aiogram.filters import CommandObject`;
- добавить команду `/period` (пресеты или ручной ввод):

```python
@router.message(Command("period"))
async def cmd_period(message: Message, command: CommandObject) -> None:
    uid = await _need_user(message, message.from_user.id)
    if not uid:
        return
    args = (command.args or "").split()
    if args:
        rng = parse_manual(args)
        if not rng:
            await message.answer("Формат: /period 2026-01 2026-03  или  /period 2026-01-01 2026-01-31")
            return
        start, end = rng
        await _period_labs(message.answer, uid, start, end, label=f"{args[0]}–{args[1]}")
        return
    await message.answer("📅 За какой период?", reply_markup=period_presets_keyboard())
```

- добавить хелпер сводки и расширить `on_callback` веткой `per` (вставить перед финальным `await cb.answer()`):

```python
async def _period_labs(target, user_id, start, end, label):
    groups = labs_in_period(user_id, start, end)
    await target(format_labs_summary(groups, label=label))
```

```python
    elif action == "per":
        preset, view = parts[0], parts[1]
        if view == "menu":
            await cb.message.edit_text(f"📅 {preset} — что показать?",
                                       reply_markup=period_view_keyboard(preset))
        else:
            start, end = preset_range(preset, now=datetime.now())
            if view == "docs":
                await _show_period_docs(cb.message.edit_text, uid, start, end, preset)
            else:
                groups = labs_in_period(uid, start, end)
                await cb.message.edit_text(format_labs_summary(groups, label=preset))
```

- добавить хелпер списка за период:

```python
async def _show_period_docs(target, user_id, start, end, preset):
    docs = documents_in_period(user_id, start, end, limit=PAGE_SIZE, offset=0)
    from botkin.bot.cards import format_list_body
    body = format_list_body(docs, offset=0, total=len(docs))
    kb = list_keyboard([d["id"] for d in docs], doc_type=None, offset=0, total=len(docs))
    await target(body, reply_markup=kb)
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_period_keyboards.py tests/test_browse.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/bot/handlers/browse.py src/botkin/bot/keyboards.py tests/test_period_keyboards.py
git commit -m "feat(bot): /period — пресеты, ручной ввод, документы и сводка за период"
```

---

## Фаза 7 — Регистрация и финал

### Task 20: Регистрация роутеров и команд в `main.py`

**Files:**
- Modify: `src/botkin/bot/main.py`
- Test: `tests/test_smoke.py` (проверка импорта роутера browse)

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_smoke.py`:

```python
def test_browse_router_importable():
    from botkin.bot.handlers import browse
    assert browse.router.name == "browse"
```

- [ ] **Step 2: Запустить**

Run: `uv run pytest tests/test_smoke.py::test_browse_router_importable -v`
Expected: PASS (модуль уже создан в Task 15)

- [ ] **Step 3: Зарегистрировать роутер и команды**

В `src/botkin/bot/main.py`:
- импорт: `from botkin.bot.handlers import browse, dynamics, help, show, start, upload`;
- зарегистрировать роутер ПОСЛЕ остальных, но `browse.router` содержит catch-all `@router.callback_query()` — он должен идти ПОСЛЕДНИМ среди роутеров: `dp.include_router(browse.router)` последней строкой среди include;
- добавить команды в `set_my_commands`:

```python
    await bot.set_my_commands([
        BotCommand(command="start", description="Регистрация"),
        BotCommand(command="help", description="Справка"),
        BotCommand(command="show", description="Последний документ"),
        BotCommand(command="list", description="Список документов"),
        BotCommand(command="period", description="Документы и показатели за период"),
        BotCommand(command="dynamics", description="График показателя"),
    ])
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/botkin/bot/main.py tests/test_smoke.py
git commit -m "feat(bot): регистрация роутера browse и команд /list, /period"
```

---

### Task 21: Финальная проверка блока D

**Files:** —

- [ ] **Step 1: Полный прогон тестов**

Run: `uv run pytest -q`
Expected: все тесты зелёные (существующие 65 + новые блока D)

- [ ] **Step 2: Линт**

Run: `uv run ruff check .`
Expected: `All checks passed!`
Если есть замечания — исправить и повторить.

- [ ] **Step 3: Проверка покрытия спеки (ручная сверка)**

Пройти по спеке `docs/superpowers/specs/2026-06-02-telegram-ux-design.md` и убедиться, что реализованы: §3 прогресс (стадии+поллинг+fallback), §4 навигация (/list, карточка, листание, фильтр), §5 период, §6 ГРЛС-вывод, §7 метаданные. Зафиксировать в коммите при необходимости.

- [ ] **Step 4: Финальный коммит (если были правки в Step 2/3)**

```bash
git add -A
git commit -m "test(bot): финальная проверка блока D — все тесты зелёные, ruff чист"
```

---

## Замечания по реализации

- **Catch-all callback:** `browse.on_callback` ловит все `callback_query`. Если позже появятся другие источники callback — заменить на фильтрацию по префиксу (`F.data.startswith(...)`). Сейчас единственный источник — навигация блока D.
- **Соседи документа** в карточке считаются по полному списку id пользователя (`list_documents(limit=10_000)`). При больших объёмах это станет неэффективно — вынести в отдельный запрос «предыдущий/следующий по дате» (вне scope D, отметить в блоке E).
- **Поллинг и БД:** бот и backend пишут одну SQLite-БД на одном хосте (WSL2, WAL). Для распределённого развёртывания понадобится API-эндпоинт статуса (вне scope D).
- **`DELIVERY_FALLBACK_DELAY`** по умолчанию 130 с (> таймаут поллинга 120 с) — fallback срабатывает только после того, как поллинг точно завершился.
