# Распознавание лабораторных анализов — План реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Анализы надёжно проходят pipeline и выводятся в Telegram-боте; распознанные названия нормализуются по справочнику ФСЛИ (каноника + LOINC + код НМУ), единицы сверяются.

**Architecture:** Повтор проверенной структуры блока B (препараты): офлайн-скрипт `xlsx → registry.jsonl`, ленивый синглтон-нормализатор на `rapidfuzz`, новые колонки в `lab_results` через декларативную миграцию, интеграция в `orchestrator._persist_lab`. Параллельно чиним промпт извлечения (односторонние референсы) и рендер карточки.

**Tech Stack:** Python 3.10, pydantic, instructor + Ollama (qwen3-vl), rapidfuzz, openpyxl/pandas, SQLite, aiogram, pytest. Менеджер — `uv`.

**Спека:** `docs/superpowers/specs/2026-06-02-lab-results-recognition-design.md`

**Команды проверки:** тесты — `uv run pytest <path> -v`; линтер — `uv run ruff check src tests`.

**Верификация на живой VLM:** Ollama в среде разработки недоступен. Весь код покрыт mock-тестами; реальный прогон `sample_020.pdf` выполняет пользователь (Task 11) и присылает логи для калибровки порогов/промпта.

---

## Файловая структура

**Создаём:**
- `scripts/build_analyte_reference.py` — сборка реестра из xlsx ФСЛИ.
- `src/botkin/reference/analytes/registry.jsonl` — артефакт сборки (генерируется в Task 7).
- `src/botkin/normalize/analytes.py` — `AnalyteNormalizer` + `AnalyteMatch`.
- `tests/test_build_analyte_reference.py`, `tests/test_normalize_analytes.py`, `tests/test_show_labs.py`.

**Модифицируем:**
- `src/botkin/db/schema.sql` — колонки `lab_results` для свежих БД.
- `src/botkin/db/connection.py` — `_MIGRATIONS["lab_results"]`.
- `src/botkin/db/queries.py:149` — расширить `SELECT` в `get_lab_results`.
- `src/botkin/config.py` — `_DEFAULTS["analytes"]` + `ANALYTE_*` константы.
- `src/botkin/llm/prompts.py` — `ANALYSIS_VLM_SYSTEM`.
- `src/botkin/domain/models.py:37,63` — `extra="forbid"` → `extra="ignore"`.
- `src/botkin/pipeline/orchestrator.py` — `_persist_lab` + синглтон нормализатора.
- `src/botkin/bot/handlers/show.py:51` — `_format_labs` + хелпер `_format_ref`.
- `tests/test_migration.py`, `tests/test_prompts.py` — расширить.

---

## Task 1: Миграция БД — новые колонки `lab_results`

**Files:**
- Modify: `src/botkin/db/connection.py:14-23` (`_MIGRATIONS`)
- Modify: `src/botkin/db/schema.sql` (CREATE TABLE lab_results)
- Test: `tests/test_migration.py`

- [ ] **Step 1: Написать падающий тест**

В конец `tests/test_migration.py` добавить:

```python
def test_lab_results_normalization_columns(set_test_db):
    """Колонки нормализации ФСЛИ и расширенных референсов добавлены в lab_results."""
    from botkin.db.connection import get_conn
    with get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(lab_results)").fetchall()}
    assert {
        "analyte_canonical", "loinc", "nmu_code", "analyte_group",
        "match_status", "unit_expected", "unit_mismatch",
        "ref_operator", "ref_text",
    } <= cols
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_migration.py::test_lab_results_normalization_columns -v`
Expected: FAIL (колонок нет).

- [ ] **Step 3: Добавить колонки в миграцию**

В `src/botkin/db/connection.py` заменить блок `"lab_results": {...}` в `_MIGRATIONS`:

```python
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
```

- [ ] **Step 4: Добавить колонки в `schema.sql` (для свежих БД)**

В `src/botkin/db/schema.sql`, в `CREATE TABLE IF NOT EXISTS lab_results (...)`, после строки `taken_at_raw TEXT,` добавить:

```sql
    ref_operator TEXT,
    ref_text TEXT,
    analyte_canonical TEXT,
    loinc TEXT,
    nmu_code TEXT,
    analyte_group TEXT,
    match_status TEXT,
    unit_expected TEXT,
    unit_mismatch INTEGER,
```

- [ ] **Step 5: Запустить тесты — убедиться, что проходят**

Run: `uv run pytest tests/test_migration.py -v`
Expected: PASS (включая `test_migration_idempotent`).

- [ ] **Step 6: Commit**

```bash
git add src/botkin/db/connection.py src/botkin/db/schema.sql tests/test_migration.py
git commit -m "feat(db): колонки нормализации ФСЛИ и расширенных референсов в lab_results"
```

---

## Task 2: Расширить `get_lab_results` (вернуть потерянные поля)

`get_lab_results` сейчас выбирает только 5 колонок — теряются `value_text`, `ref_operator`, `ref_text` и новые поля нормализации. Без этого рендер (Task 5) не увидит данные.

**Files:**
- Modify: `src/botkin/db/queries.py:149-156`
- Test: `tests/test_queries_ui.py`

- [ ] **Step 1: Написать падающий тест**

В конец `tests/test_queries_ui.py` добавить:

```python
def test_get_lab_results_returns_extended_fields(set_test_db):
    from botkin.db.connection import get_conn
    from botkin.db.queries import get_lab_results
    from botkin.db.repos import DocumentRepo, UserRepo
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(7001)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/a.jpg")
        conn.execute(
            "INSERT INTO lab_results(document_id, user_id, analyte_name, value_text, "
            "ref_operator, ref_high, ref_text, analyte_canonical, loinc, match_status, "
            "unit_expected, unit_mismatch) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (did, uid, "СРБ", None, "<", 5.0, None, "С-реактивный белок",
             "1988-5", "matched", "мг/л", 0),
        )
    rows = get_lab_results(did)
    r = rows[0]
    assert r["value_text"] is None and r["ref_operator"] == "<"
    assert r["analyte_canonical"] == "С-реактивный белок"
    assert r["loinc"] == "1988-5" and r["match_status"] == "matched"
    assert r["unit_expected"] == "мг/л" and r["unit_mismatch"] == 0
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_queries_ui.py::test_get_lab_results_returns_extended_fields -v`
Expected: FAIL (KeyError на `value_text`).

- [ ] **Step 3: Расширить SELECT**

В `src/botkin/db/queries.py` заменить тело `get_lab_results`:

```python
def get_lab_results(document_id: int, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT analyte_name, value_num, value_text, unit, "
            "ref_low, ref_high, ref_operator, ref_text, "
            "analyte_canonical, loinc, nmu_code, analyte_group, "
            "match_status, unit_expected, unit_mismatch "
            "FROM lab_results WHERE document_id = ? LIMIT ?",
            (document_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_queries_ui.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/botkin/db/queries.py tests/test_queries_ui.py
git commit -m "feat(db): get_lab_results возвращает value_text, операторы референса и поля нормализации"
```

---

## Task 3: Промпт извлечения + снятие `extra="forbid"`

**Files:**
- Modify: `src/botkin/llm/prompts.py:17-27` (`ANALYSIS_VLM_SYSTEM`)
- Modify: `src/botkin/domain/models.py:37` и `:63` (`ConfigDict`)
- Test: `tests/test_prompts.py`

- [ ] **Step 1: Написать падающие тесты**

В конец `tests/test_prompts.py` добавить:

```python
def test_analysis_prompt_covers_one_sided_refs_and_value_text():
    from botkin.llm import prompts
    p = prompts.ANALYSIS_VLM_SYSTEM
    assert "ref_operator" in p          # односторонние референсы описаны
    assert "<5.0" in p or "<" in p
    assert "value_text" in p
    assert "ref_text" in p

def test_lab_result_ignores_extra_fields():
    """Лишнее поле от модели не должно ломать парсинг (extra=ignore)."""
    from botkin.domain.models import LabResult
    m = LabResult.model_validate(
        {"analyte_name": "Глюкоза", "value_num": 5.4, "unit": "ммоль/л", "foo": "bar"}
    )
    assert m.analyte_name == "Глюкоза" and m.value_num == 5.4
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: оба новых теста FAIL.

- [ ] **Step 3: Обновить промпт**

В `src/botkin/llm/prompts.py` заменить `ANALYSIS_VLM_SYSTEM`:

```python
ANALYSIS_VLM_SYSTEM = """Ты — медицинский ассистент, который ТОЧНО извлекает показатели из лабораторных анализов.

Правила:
1. Извлекай ВСЕ строки таблицы с показателями — не пропускай ни одной. Заголовки таблиц пропускай.
2. Единицы сохраняй как в документе: "г/л", "ммоль/л", "%", "×10⁹/л".
3. Двусторонний диапазон ("4.0-5.5") — это референс: ref_low=4.0, ref_high=5.5.
4. Односторонний референс с оператором:
   - "<5.0" → ref_operator="<", ref_high=5.0, ref_low=null;
   - ">120" → ref_operator=">", ref_low=120, ref_high=null;
   - "≤" и "≥" трактуй как "<" и ">".
5. Текстовая норма ("отрицательно", "не обнаружено") → в ref_text, числовые ref_low/ref_high=null.
6. analyte_name — на русском как в документе; analyte_code — на английском (HGB, RBC, GLU), если узнаёшь.
7. value_num — только число. Текстовый результат ("не обнаружено", "+", "++") → в value_text, value_num=null.
   value_num и value_text взаимоисключающи: одно из них всегда null.
8. Сохраняй десятичные разделители как в оригинале и флаги «*», «↑», «↓», «(+)».
9. taken_at — дата забора из шапки документа, повторяй для каждой строки.
10. Отсутствующее поле — null."""
```

- [ ] **Step 4: Снять `extra="forbid"`**

В `src/botkin/domain/models.py` в классе `LabResult` (строка 37) и `DoctorReport` (строка 63) заменить:

```python
    model_config = ConfigDict(extra="ignore")
```

(было `extra="forbid"` — заменить в обоих классах).

- [ ] **Step 5: Запустить — убедиться, что проходят**

Run: `uv run pytest tests/test_prompts.py tests/test_llm_calls.py -v`
Expected: PASS (включая существующие).

- [ ] **Step 6: Commit**

```bash
git add src/botkin/llm/prompts.py src/botkin/domain/models.py tests/test_prompts.py
git commit -m "feat(llm): односторонние референсы и value_text в промпте; extra=ignore против падений извлечения"
```

---

## Task 4: Починка рендера `_format_labs`

**Files:**
- Modify: `src/botkin/bot/handlers/show.py:51-64`
- Test: `tests/test_show_labs.py` (создать)

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_show_labs.py`:

```python
from botkin.bot.handlers.show import _format_labs, _format_ref


def _row(**kw):
    base = dict(analyte_name="X", value_num=None, value_text=None, unit=None,
                ref_low=None, ref_high=None, ref_operator=None, ref_text=None,
                analyte_canonical=None, loinc=None, nmu_code=None, analyte_group=None,
                match_status=None, unit_expected=None, unit_mismatch=None)
    base.update(kw)
    return base


def test_text_result_rendered_not_none():
    out = _format_labs([_row(analyte_name="Антитела", value_text="не обнаружено")])
    assert "не обнаружено" in out
    assert "None" not in out


def test_one_sided_ref_shown():
    out = _format_labs([_row(analyte_name="СРБ", value_num=1.8, unit="мг/л",
                             ref_operator="<", ref_high=5.0)])
    assert "1.8" in out and "<5.0" in out


def test_two_sided_ref_and_high_marker():
    out = _format_labs([_row(analyte_name="Глюкоза", value_num=7.0, unit="ммоль/л",
                             ref_low=3.9, ref_high=6.1)])
    assert "3.9" in out and "6.1" in out and "⬆️" in out


def test_low_marker_with_operator_ref():
    # value ниже нижней границы ">120"
    out = _format_labs([_row(analyte_name="X", value_num=100.0,
                             ref_operator=">", ref_low=120.0)])
    assert "⬇️" in out


def test_text_ref_shown():
    out = _format_labs([_row(analyte_name="HBsAg", value_text="отрицательно",
                             ref_text="отрицательно")])
    assert "отрицательно" in out


def test_unit_mismatch_warning():
    out = _format_labs([_row(analyte_name="Глюкоза", value_num=5.4, unit="г/л",
                             unit_expected="ммоль/л", unit_mismatch=1)])
    assert "⚠️" in out


def test_empty_rows():
    assert _format_labs([]) == "—"


def test_format_ref_helper():
    assert _format_ref(_row(ref_low=3.9, ref_high=6.1)) == "норма 3.9–6.1"
    assert _format_ref(_row(ref_operator="<", ref_high=5.0)) == "норма <5.0"
    assert _format_ref(_row(ref_operator=">", ref_low=120.0)) == "норма >120.0"
    assert _format_ref(_row(ref_text="отрицательно")) == "норма: отрицательно"
    assert _format_ref(_row()) == ""
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `uv run pytest tests/test_show_labs.py -v`
Expected: FAIL (`_format_ref` не существует; старый `_format_labs` не покрывает кейсы).

- [ ] **Step 3: Переписать рендер**

В `src/botkin/bot/handlers/show.py` заменить `_format_labs` (строки 51-64) на:

```python
def _format_ref(r: dict) -> str:
    """Текст нормы: двусторонняя / односторонняя с оператором / текстовая."""
    if r.get("ref_low") is not None and r.get("ref_high") is not None:
        return f"норма {r['ref_low']}–{r['ref_high']}"
    op = r.get("ref_operator")
    if op == "<" and r.get("ref_high") is not None:
        return f"норма <{r['ref_high']}"
    if op == ">" and r.get("ref_low") is not None:
        return f"норма >{r['ref_low']}"
    if r.get("ref_text"):
        return f"норма: {r['ref_text']}"
    return ""


def _ref_marker(r: dict) -> str:
    """⬆️/⬇️ по доступным границам (в т.ч. односторонним)."""
    v = r.get("value_num")
    if v is None:
        return ""
    low, high = r.get("ref_low"), r.get("ref_high")
    if low is not None and v < low:
        return " ⬇️"
    if high is not None and v > high:
        return " ⬆️"
    return ""


def _format_labs(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        if r.get("value_num") is not None:
            value = f"{r['value_num']}"
        elif r.get("value_text"):
            value = html.escape(r["value_text"])
        else:
            continue
        name = html.escape(r.get("analyte_canonical") or r["analyte_name"])
        unit = f" {html.escape(r['unit'])}" if r.get("unit") else ""
        ref = _format_ref(r)
        ref = f" ({ref})" if ref else ""
        warn = " ⚠️" if r.get("unit_mismatch") else ""
        marker = _ref_marker(r)
        lines.append(f"• <b>{name}</b>: {value}{unit}{ref}{marker}{warn}")
    return "\n".join(lines) or "—"
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `uv run pytest tests/test_show_labs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/botkin/bot/handlers/show.py tests/test_show_labs.py
git commit -m "fix(bot): рендер анализов — текстовые результаты, односторонние нормы, флаг единицы"
```

---

## Task 5: Скрипт сборки реестра ФСЛИ

**Files:**
- Create: `scripts/build_analyte_reference.py`
- Test: `tests/test_build_analyte_reference.py`

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_build_analyte_reference.py`:

```python
from scripts.build_analyte_reference import row_to_record, normalize_key, STATUS_MAP


HEADER = [
    "ID", "LOINC", "FULLNAME", "ENGLISHNAME", "SHORTNAME", "SYNONYMS", "ANALYTE",
    "SPECANALYTE", "MEASUREMENT", "UNIT", "SPECIMEN", "TIMECHAR", "METHODTYPE",
    "SCALETYPE", "TESTSTATUS", "GROUP", "NMU", "SORT",
]


def _row(**kw):
    d = {h: None for h in HEADER}
    d.update(kw)
    return d


def test_row_to_record_basic():
    rec = row_to_record(_row(
        LOINC="14979-9", FULLNAME="АЧТВ исследование", SHORTNAME="АЧТВ",
        ENGLISHNAME="APTT", SYNONYMS="АПТВ; Activated PTT", UNIT="с",
        GROUP="Коагулогические исследования", TESTSTATUS="Актуальный", NMU="A12.05.039",
    ))
    assert rec["name"] == "АЧТВ исследование"
    assert rec["short"] == "АЧТВ"
    assert rec["loinc"] == "14979-9"
    assert rec["nmu"] == "A12.05.039"
    assert rec["unit"] == "с"
    assert rec["group"] == "Коагулогические исследования"
    assert rec["status"] == "active"
    assert "АПТВ" in rec["synonyms"] and "Activated PTT" in rec["synonyms"]


def test_status_mapping():
    assert STATUS_MAP["Актуальный"] == "active"
    assert STATUS_MAP["Новый"] == "new"
    assert STATUS_MAP["Устаревший"] == "deprecated"


def test_loinc_zero_becomes_null():
    rec = row_to_record(_row(FULLNAME="Тест", LOINC="0", TESTSTATUS="Актуальный"))
    assert rec["loinc"] is None


def test_empty_fullname_skipped():
    assert row_to_record(_row(FULLNAME=None, TESTSTATUS="Актуальный")) is None


def test_normalize_key():
    assert normalize_key("  Гёмоглобин  Общий ") == "гемоглобин общий"
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_build_analyte_reference.py -v`
Expected: FAIL (модуль не существует).

- [ ] **Step 3: Реализовать скрипт**

Создать `scripts/build_analyte_reference.py`:

```python
"""Сборка структурного справочника анализов из выгрузки ФСЛИ (xlsx).

Источник: «Справочник лабораторных тестов» ФСЛИ (OID 1.2.643.5.1.13.13.11.1080,
портал НСИ Минздрава). Один лист «Справочник», шапка — строка 2 (1-based), данные с строки 3.
Значимые колонки: LOINC, FULLNAME, ENGLISHNAME, SHORTNAME, SYNONYMS (через ';'),
UNIT, GROUP, TESTSTATUS, NMU.

Результат — registry.jsonl: по записи на тест с каноничным именем, краткой/английской формой,
синонимами, LOINC, кодом НМУ, единицей, группой и статусом. Позволяет в рантайме фаззи-коррекцию
названия по полному набору форм и заполнение LOINC/НМУ/ожидаемой единицы.

Запуск (сеть НЕ требуется):
    uv run python -m scripts.build_analyte_reference \\
        --src "Справочник лабораторных тестов.xlsx" \\
        --out src/botkin/reference/analytes/registry.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import openpyxl

STATUS_MAP = {"Актуальный": "active", "Новый": "new", "Устаревший": "deprecated"}
_MIN_NAME_LEN = 2
_HEADER_ROW = 2  # 1-based: строка с именами колонок


def normalize_key(name: str) -> str:
    """Ключ дедупликации/матчинга: lower, ё→е, схлопывание пробелов."""
    return " ".join(str(name).strip().lower().replace("ё", "е").split())


def _clean(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _split_synonyms(value) -> list[str]:
    if value is None:
        return []
    return [s.strip() for s in str(value).split(";") if s.strip()]


def row_to_record(row: dict) -> dict | None:
    """Строка xlsx (dict по именам колонок) → запись реестра или None (если нет имени)."""
    full = _clean(row.get("FULLNAME"))
    if not full or len(full) < _MIN_NAME_LEN:
        return None
    loinc = _clean(row.get("LOINC"))
    if loinc == "0":
        loinc = None
    status_raw = _clean(row.get("TESTSTATUS")) or ""
    return {
        "name": full,
        "short": _clean(row.get("SHORTNAME")),
        "english": _clean(row.get("ENGLISHNAME")),
        "synonyms": _split_synonyms(row.get("SYNONYMS")),
        "loinc": loinc,
        "nmu": _clean(row.get("NMU")),
        "unit": _clean(row.get("UNIT")),
        "group": _clean(row.get("GROUP")),
        "specimen": _clean(row.get("SPECIMEN")),
        "status": STATUS_MAP.get(status_raw, status_raw.lower()),
    }


def build_registry(xlsx_path: Path) -> list[dict]:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb["Справочник"] if "Справочник" in wb.sheetnames else wb.active
    rows = ws.iter_rows(values_only=True)
    header = None
    for i, row in enumerate(rows, start=1):
        if i == _HEADER_ROW:
            header = [str(c).strip() if c is not None else "" for c in row]
            break
    if header is None:
        wb.close()
        return []

    seen: set[str] = set()
    out: list[dict] = []
    for row in rows:  # продолжаем с данных (после шапки)
        record = row_to_record(dict(zip(header, row)))
        if record is None:
            continue
        key = normalize_key(record["name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    wb.close()
    return out


def write_registry(records: list[dict], out_path: Path, source_note: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": source_note}, ensure_ascii=False) + "\n")
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:  # pragma: no cover — ручной запуск с файлом выгрузки
    parser = argparse.ArgumentParser(description="Сборка справочника анализов botkin из ФСЛИ")
    parser.add_argument("--src", type=Path, required=True, help="xlsx-выгрузка ФСЛИ")
    parser.add_argument("--out", type=Path, required=True, help="Путь к registry.jsonl")
    args = parser.parse_args()

    records = build_registry(args.src)
    write_registry(records, args.out, source_note=f"ФСЛИ {args.src.name} ({len(records)})")
    print(f"Записано {len(records)} тестов в {args.out}")


if __name__ == "__main__":  # pragma: no cover
    main()
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `uv run pytest tests/test_build_analyte_reference.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/build_analyte_reference.py tests/test_build_analyte_reference.py
git commit -m "feat(reference): скрипт сборки справочника анализов из ФСЛИ (xlsx → jsonl)"
```

---

## Task 6: Сгенерировать `registry.jsonl` из xlsx

**Files:**
- Create (артефакт): `src/botkin/reference/analytes/registry.jsonl`

- [ ] **Step 1: Запустить сборку**

Run:
```bash
uv run python -m scripts.build_analyte_reference \
  --src "Справочник лабораторных тестов.xlsx" \
  --out src/botkin/reference/analytes/registry.jsonl
```
Expected: вывод `Записано ~20727 тестов в ...` (число близко к 20727 за вычетом дублей по имени).

- [ ] **Step 2: Проверить артефакт**

Run:
```bash
head -3 src/botkin/reference/analytes/registry.jsonl
wc -l src/botkin/reference/analytes/registry.jsonl
```
Expected: первая строка — `{"_meta": "ФСЛИ ..."}`; далее записи с `name`/`loinc`/`synonyms`; строк ≈ число тестов + 1.

- [ ] **Step 3: Commit**

```bash
git add -f src/botkin/reference/analytes/registry.jsonl
git commit -m "feat(reference): сгенерированный справочник анализов ФСЛИ (registry.jsonl)"
```

---

## Task 7: Пороги нормализатора в config

**Files:**
- Modify: `src/botkin/config.py:64-67` (`_DEFAULTS`), и блок констант (~строка 137)
- Test: `tests/test_config_analytes.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_config_analytes.py`:

```python
def test_analyte_thresholds_exported():
    from botkin import config
    assert isinstance(config.ANALYTE_MAX_EDIT_RATIO, float)
    assert isinstance(config.ANALYTE_RATIO_FLOOR, float)
    assert 0.0 < config.ANALYTE_MAX_EDIT_RATIO < 1.0
    assert 50 <= config.ANALYTE_RATIO_FLOOR <= 100
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_config_analytes.py -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Добавить дефолты и константы**

В `src/botkin/config.py` в `_DEFAULTS` после блока `"drugs": {...}` добавить:

```python
    "analytes": {
        "max_edit_ratio": 0.35,
        "ratio_floor": 75,
    },
```

После блока `DRUG_RATIO_FLOOR = ...` (строка ~136) добавить:

```python
# ── Нормализация анализов (ФСЛИ) ──────────────────────────────────────────────
# Аналогично препаратам: cap по дистанции Дамерау-Левенштейна + ratio-floor.
ANALYTE_MAX_EDIT_RATIO = float(_get("analytes.max_edit_ratio", _DEFAULTS["analytes"]["max_edit_ratio"]))
ANALYTE_RATIO_FLOOR = float(_get("analytes.ratio_floor", _DEFAULTS["analytes"]["ratio_floor"]))
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_config_analytes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/botkin/config.py tests/test_config_analytes.py
git commit -m "feat(config): пороги нормализации анализов ANALYTE_MAX_EDIT_RATIO/RATIO_FLOOR"
```

---

## Task 8: `AnalyteNormalizer`

**Files:**
- Create: `src/botkin/normalize/analytes.py`
- Test: `tests/test_normalize_analytes.py`

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_normalize_analytes.py`:

```python
from botkin.normalize.analytes import AnalyteNormalizer


def _rec(name, short=None, english=None, synonyms=(), loinc=None, nmu=None,
         unit=None, group=None, status="active"):
    return {"name": name, "short": short, "english": english,
            "synonyms": list(synonyms), "loinc": loinc, "nmu": nmu,
            "unit": unit, "group": group, "status": status}


def _norm(records=None):
    records = records or [
        _rec("Гемоглобин", short="HGB", synonyms=["Hb"], loinc="718-7",
             nmu="B03.016.003", unit="г/л", group="Гематологические исследования"),
        _rec("Глюкоза", short="GLU", english="Glucose", loinc="2345-7",
             unit="ммоль/л", group="Биохимические исследования"),
        _rec("С-реактивный белок", short="СРБ", synonyms=["CRP"], loinc="1988-5",
             unit="мг/л", group="Биохимические исследования"),
    ]
    return AnalyteNormalizer(records)


def test_exact_match():
    m = _norm().correct("Гемоглобин")
    assert m.canonical == "Гемоглобин" and m.status == "matched" and m.distance == 0
    assert m.loinc == "718-7" and m.nmu == "B03.016.003" and m.expected_unit == "г/л"


def test_ocr_typo_corrected():
    m = _norm().correct("Глюкоэа")          # OCR з→э
    assert m.canonical == "Глюкоза" and m.status == "matched"


def test_match_by_synonym():
    assert _norm().correct("CRP").canonical == "С-реактивный белок"


def test_match_by_short_form():
    assert _norm().correct("СРБ").canonical == "С-реактивный белок"


def test_short_abbreviation_requires_exact():
    # «HGB» точно совпадает с короткой формой
    assert _norm().correct("HGB").canonical == "Гемоглобин"
    # случайные 3 буквы не должны прилепляться к короткой форме
    assert _norm().correct("XYZ").status == "unverified"


def test_unknown_not_snapped():
    m = _norm().correct("Неведомыйпоказательксено")
    assert m.status == "unverified" and m.canonical is None
    assert m.raw == "Неведомыйпоказательксено"


def test_raw_preserved():
    assert _norm().correct("Глюкоэа").raw == "Глюкоэа"


def test_status_carried():
    n = _norm([_rec("Старый тест", status="deprecated")])
    assert n.correct("Старый тест").match_status == "deprecated"
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `uv run pytest tests/test_normalize_analytes.py -v`
Expected: FAIL (модуль не существует).

- [ ] **Step 3: Реализовать нормализатор**

Создать `src/botkin/normalize/analytes.py`:

```python
"""Фаззи-коррекция названий анализов по справочнику ФСЛИ (registry.jsonl).

По образцу normalize/drugs.py: scorer — абсолютная дистанция Дамерау-Левенштейна
(устойчива к OCR-ошибкам), плюс ratio-floor. Несовпавшее имя НЕ подменяется (status='unverified').

Каждая запись разворачивается в несколько поисковых ключей (полное/краткое/английское имя,
синонимы) → одна каноничная запись. Короткие ключи (аббревиатуры ≤3 символов) требуют точного
совпадения, иначе фаззи на 2-3 символах даёт мусор.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rapidfuzz import distance, fuzz, process

from botkin.config import ANALYTE_MAX_EDIT_RATIO, ANALYTE_RATIO_FLOOR

_REGISTRY_PATH = Path(__file__).parent.parent / "reference" / "analytes" / "registry.jsonl"
_SHORT_KEY_LEN = 3  # ключи такой длины и короче требуют точного совпадения


@dataclass(frozen=True)
class AnalyteMatch:
    raw: str
    canonical: str | None
    loinc: str | None
    nmu: str | None
    group: str | None
    expected_unit: str | None
    status: str            # "matched" | "unverified"
    match_status: str | None   # статус теста в реестре: active | new | deprecated
    distance: int | None
    ratio: float


def _normalize_name(name: str) -> str:
    return " ".join(name.strip().lower().replace("ё", "е").split())


def _unverified(raw: str, dist: int | None = None, ratio: float = 0.0) -> AnalyteMatch:
    return AnalyteMatch(raw=raw, canonical=None, loinc=None, nmu=None, group=None,
                        expected_unit=None, status="unverified", match_status=None,
                        distance=dist, ratio=ratio)


class AnalyteNormalizer:
    """Сверяет распознанные названия анализов со справочником ФСЛИ через RapidFuzz."""

    def __init__(
        self,
        records: Iterable[dict],
        max_edit_ratio: float = ANALYTE_MAX_EDIT_RATIO,
        ratio_floor: float = ANALYTE_RATIO_FLOOR,
    ):
        self._max_edit_ratio = max_edit_ratio
        self._ratio_floor = ratio_floor
        # Поисковый ключ → каноничная запись. Первый победитель остаётся.
        self._by_key: dict[str, dict] = {}
        for record in records:
            forms = [record.get("name"), record.get("short"), record.get("english")]
            forms.extend(record.get("synonyms", []))
            for form in forms:
                if not form:
                    continue
                key = _normalize_name(form)
                if key and key not in self._by_key:
                    self._by_key[key] = record
        self._choices: list[str] = list(self._by_key)

    def _result(self, raw_name: str, record: dict, dist: int, ratio: float) -> AnalyteMatch:
        return AnalyteMatch(
            raw=raw_name,
            canonical=record["name"],
            loinc=record.get("loinc"),
            nmu=record.get("nmu"),
            group=record.get("group"),
            expected_unit=record.get("unit"),
            status="matched",
            match_status=record.get("status"),
            distance=dist,
            ratio=ratio,
        )

    def correct(self, raw_name: str) -> AnalyteMatch:
        query = _normalize_name(raw_name)
        if not query or not self._choices:
            return _unverified(raw_name)

        # Короткие ключи (аббревиатуры) — только точное совпадение.
        if len(query) <= _SHORT_KEY_LEN:
            record = self._by_key.get(query)
            if record is not None:
                return self._result(raw_name, record, 0, 100.0)
            return _unverified(raw_name)

        cap = max(1, math.floor(len(query) * self._max_edit_ratio))
        best = process.extractOne(
            query, self._choices,
            scorer=distance.DamerauLevenshtein.distance,
            score_cutoff=cap,
        )
        if best is None:
            return _unverified(raw_name)

        matched_key, dist, _ = best
        ratio = fuzz.ratio(query, matched_key)
        if ratio < self._ratio_floor:
            return _unverified(raw_name, dist=int(dist), ratio=ratio)
        return self._result(raw_name, self._by_key[matched_key], int(dist), ratio)


def _read_registry(path: Path = _REGISTRY_PATH) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if "_meta" in obj:
            continue
        records.append(obj)
    return records


def load_default() -> AnalyteNormalizer:
    return AnalyteNormalizer(_read_registry())
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `uv run pytest tests/test_normalize_analytes.py -v`
Expected: PASS.

- [ ] **Step 5: Добавить тест на реальном реестре**

В конец `tests/test_normalize_analytes.py` добавить:

```python
def test_loader_reads_packaged_registry():
    from botkin.normalize.analytes import load_default
    n = load_default()
    assert n.correct("Гемоглобин").canonical is not None
    assert n.correct("Глюкоза").status == "matched"
```

- [ ] **Step 6: Запустить и закоммитить**

Run: `uv run pytest tests/test_normalize_analytes.py -v`
Expected: PASS.

```bash
git add src/botkin/normalize/analytes.py tests/test_normalize_analytes.py
git commit -m "feat(normalize): AnalyteNormalizer — фаззи-коррекция названий анализов по ФСЛИ"
```

---

## Task 9: Интеграция в pipeline + проверка единицы

**Files:**
- Modify: `src/botkin/pipeline/orchestrator.py` (импорт, синглтон, `_persist_lab`)
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Написать падающий тест**

В конец `tests/test_orchestrator.py` добавить:

```python
def test_persist_lab_normalizes_and_checks_unit(set_test_db, monkeypatch):
    from botkin.db.connection import get_conn
    from botkin.db.repos import DocumentRepo, UserRepo
    from botkin.domain.models import LabResult
    from botkin.normalize.analytes import AnalyteNormalizer
    from botkin.pipeline import orchestrator

    # Детерминированный нормализатор (не зависим от содержимого реального реестра).
    fake = AnalyteNormalizer([
        {"name": "Глюкоза", "short": "GLU", "english": "Glucose", "synonyms": [],
         "loinc": "2345-7", "nmu": "B03.016.006", "unit": "ммоль/л",
         "group": "Биохимические исследования", "status": "active"},
    ])
    monkeypatch.setattr(orchestrator, "_ANALYTE_NORMALIZER", fake)

    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(9100)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/a.jpg")

    items = [
        LabResult(analyte_name="Глюкоэа", value_num=5.4, unit="г/л"),  # опечатка + неверная единица
    ]
    orchestrator._persist_lab(did, uid, items)

    with get_conn() as conn:
        row = conn.execute(
            "SELECT analyte_name, analyte_canonical, match_status, loinc, "
            "unit_expected, unit_mismatch FROM lab_results WHERE document_id=?",
            (did,),
        ).fetchone()
    assert row["analyte_name"] == "Глюкоэа"          # исходное имя не перезаписано
    assert row["analyte_canonical"] == "Глюкоза"      # нормализовано
    assert row["match_status"] == "matched"
    assert row["unit_expected"] == "ммоль/л"
    assert row["unit_mismatch"] == 1                  # г/л ≠ ммоль/л
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_orchestrator.py::test_persist_lab_normalizes_and_checks_unit -v`
Expected: FAIL (нет нормализации; колонки пусты).

- [ ] **Step 3: Реализовать интеграцию**

В `src/botkin/pipeline/orchestrator.py`:

3a. Добавить импорт рядом с импортом drugs (после строки 13):

```python
from botkin.normalize.analytes import AnalyteNormalizer, load_default as load_analytes
```

3b. Добавить синглтон рядом с `_DRUG_NORMALIZER` (после `get_drug_normalizer`):

```python
_ANALYTE_NORMALIZER: AnalyteNormalizer | None = None


def get_analyte_normalizer() -> AnalyteNormalizer:
    """Ленивый синглтон: справочник анализов ФСЛИ читается из registry.jsonl один раз."""
    global _ANALYTE_NORMALIZER
    if _ANALYTE_NORMALIZER is None:
        _ANALYTE_NORMALIZER = load_analytes()
    return _ANALYTE_NORMALIZER
```

3c. Заменить `_persist_lab` целиком:

```python
def _persist_lab(document_id: int, user_id: int, items: list[LabResult]) -> None:
    normalizer = get_analyte_normalizer()
    with get_conn() as conn:
        for item in items:
            unit_canon, unit_raw = canonical_unit(item.unit)
            match = normalizer.correct(item.analyte_name)
            unit_mismatch = None
            if match.status == "matched" and match.expected_unit and unit_canon:
                exp_canon, _ = canonical_unit(match.expected_unit)
                unit_mismatch = 1 if exp_canon != unit_canon else 0
            conn.execute(
                """INSERT INTO lab_results(document_id, user_id, analyte_code, analyte_name,
                   value_num, value_text, unit, ref_low, ref_high, ref_operator, ref_text,
                   taken_at, source_table_cell, value_raw, unit_raw, taken_at_raw,
                   analyte_canonical, loinc, nmu_code, analyte_group, match_status,
                   unit_expected, unit_mismatch)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (document_id, user_id, item.analyte_code, item.analyte_name,
                 item.value_num, item.value_text, unit_canon,
                 item.ref_low, item.ref_high, item.ref_operator, item.ref_text,
                 item.taken_at.isoformat() if item.taken_at else None,
                 item.source_table_cell,
                 item.value_raw, unit_raw, item.taken_at_raw,
                 match.canonical, match.loinc, match.nmu, match.group,
                 match.status, match.expected_unit, unit_mismatch),
            )
        conn.commit()
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: PASS (включая существующие тесты orchestrator).

- [ ] **Step 5: Commit**

```bash
git add src/botkin/pipeline/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(pipeline): нормализация анализов по ФСЛИ и проверка единицы в _persist_lab"
```

---

## Task 10: Финальная проверка (mock-уровень)

**Files:** —

- [ ] **Step 1: Прогнать весь тест-сьют**

Run: `uv run pytest -q`
Expected: все тесты зелёные (115+ существующих и новые).

- [ ] **Step 2: Линтер**

Run: `uv run ruff check src tests`
Expected: чисто (при замечаниях — поправить и повторить).

- [ ] **Step 3: Commit (если были правки линтера)**

```bash
git add -A
git commit -m "chore: ruff fixes по блоку анализов"
```

---

## Task 11: Диагностика и калибровка на живой Ollama (выполняет пользователь)

Этот шаг требует запущенной Ollama с `qwen3-vl` — выполняется на машине пользователя.
Агент не может его проверить; задача — собрать факты и при необходимости скорректировать.

- [ ] **Step 1: Прогнать реальный документ**

Пользователь загружает `sample_020.pdf` через бота/API и наблюдает логи pipeline
(`[START_EXTRACT]`/`[SUCCESS_EXTRACT]`/`[FAILED_EXTRACT]`, строку classify
`Doc N classified as ...`).

- [ ] **Step 2: Зафиксировать корень дефекта**

По логам определить, где раньше терялись анализы:
- если `classified as unknown/doctor_report` — проблема классификации → уточнить
  `CLASSIFY_VLM_SYSTEM` (примеры бланков лабораторий);
- если `[FAILED_EXTRACT]` с ошибкой валидации — подтверждается гипотеза `extra` (уже снято в Task 3);
- если `lab_results` заполнилась и карточка показывает СРБ с нормой `<5.0` — дефект устранён.

- [ ] **Step 3: Калибровать пороги нормализатора**

Если на реальных бланках встречаются ложные совпадения или пропуски — подстроить
`ANALYTE_MAX_EDIT_RATIO`/`ANALYTE_RATIO_FLOOR` в `config.json` (без правки кода) и сообщить
итоговые значения для фиксации в `_DEFAULTS`.

- [ ] **Step 4: Зафиксировать результат**

Закоммитить откалиброванные дефолты/уточнённый промпт (если потребовались):

```bash
git add -A
git commit -m "tune(llm): калибровка классификации/порогов анализов по реальным бланкам"
```

---

## Самопроверка плана

- **Покрытие спеки:** A→Task5/6, B→Task7/8, C→Task9, D→Task1/2, E→Task3, F→Task4,
  диагностика→Task11, верификация→Task10/11. Все блоки покрыты.
- **Типы согласованы:** `AnalyteMatch` (canonical/loinc/nmu/group/expected_unit/status/match_status)
  используется одинаково в Task 8 и Task 9; `_format_ref`/`_format_labs` — в Task 4; колонки БД
  совпадают между Task 1 (миграция), Task 2 (SELECT), Task 9 (INSERT).
- **Без плейсхолдеров:** каждый шаг с кодом содержит полный код.
```
