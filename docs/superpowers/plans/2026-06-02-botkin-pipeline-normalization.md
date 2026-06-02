# Botkin Pipeline + Normalization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ускорить распознавание медицинских документов (<30 с) переходом на `qwen3-vl:8b-instruct` + препроцессинг изображений, и устранить ошибки в данных (особенно названиях лекарств) добавлением слоя нормализации с офлайн-справочником и фаззи-коррекцией.

**Architecture:** В конвейер `classify → extract → persist` добавляется препроцессинг изображений (новый слой `preprocess/`) перед VLM и слой нормализации (`normalize/`) между извлечением и сохранением. Справочник лекарств (`reference/`) собирается build-скриптом из ГРЛС+ЕАЭС и используется офлайн. Сырые данные модели сохраняются целиком для восстановимости.

**Tech Stack:** Python 3.12, FastAPI, aiogram, Ollama (qwen3-vl:8b-instruct) + instructor, PyMuPDF, Pillow + pillow-heif, RapidFuzz 3.14.5, SQLite, pytest.

> **КРИТИЧНО — окружение разработки.** На dev-сервере **НЕТ GPU и моделей Ollama**.
> Никогда не запускать VLM-код, классификацию/извлечение или бенчмарки скорости здесь.
> **Все автотесты мокают LLM-клиент.** Латентность (цель <30 с) проверяет пользователь
> локально. В CI проверяем препроцессинг, нормализацию, парсинг, фаззи-коррекцию, миграцию,
> импорты.

**Спек:** `docs/superpowers/specs/2026-06-02-pipeline-recognition-and-normalization-design.md`
**Ветка:** `spec/botkin-pipeline-normalization` (уже создана).

---

## Карта файлов

```
src/botkin/
├── config.py                      MODIFY  +константы изображений/ollama/фаззи, фиксы
├── preprocess/__init__.py         CREATE
├── preprocess/images.py           CREATE  PDF/фото → подготовленные JPEG-байты (+base64)
├── normalize/__init__.py          CREATE
├── normalize/numbers.py           CREATE  десятичная запятая→точка, парс чисел
├── normalize/dates.py             CREATE  множество форматов дат → ISO (+сырое)
├── normalize/units.py             CREATE  канонизация единиц измерения
├── normalize/drugs.py             CREATE  DrugNormalizer: фаззи-коррекция + статусы/МНН/рег-номера
├── reference/__init__.py          CREATE
├── reference/drugs/registry.jsonl CREATE  структурный справочник 20 948 названий (ГРЛС ZIP) — собран и закоммичен
├── reference/units.py             CREATE  таблица соответствий единиц
├── llm/client.py                  MODIFY  +keep_alive
├── llm/prompts.py                 MODIFY  убрать анти-thinking костыли
├── llm/classify.py                MODIFY  дешёвый classify через preprocess
├── llm/extract.py                 MODIFY  препроцессинг + параметры из config
├── pipeline/orchestrator.py       MODIFY  +этап normalize, persist пишет raw_*
├── domain/models.py               MODIFY  +*_raw поля; parse_ru_date → normalize.dates
└── db/schema.sql                  MODIFY  +ADD COLUMN (миграция)
scripts/
└── build_drug_reference.py        CREATE  ГРЛС ZIP (8 листов, openpyxl) → registry.jsonl
tests/
├── test_normalize_numbers.py      CREATE
├── test_normalize_dates.py        CREATE
├── test_normalize_units.py        CREATE
├── test_normalize_drugs.py        CREATE
├── test_preprocess_images.py      CREATE
├── test_prompts.py                CREATE
├── test_llm_calls.py              CREATE  (мок client)
├── test_migration.py              CREATE
└── test_orchestrator.py           CREATE  (мок classify/extract)
```

---

## Task 1: Зависимости

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Добавить зависимости рантайма**

В `pyproject.toml` в массив `dependencies` добавить (RapidFuzz версия зафиксирована по
PyPI на 2026-06; pillow-heif 1.x — только HEIC-декодер, AVIF им больше не нужен):

```toml
    "rapidfuzz==3.14.5",
    "pillow-heif>=1.3.0",
```

А в `[dependency-groups].dev` добавить (build-скрипт справочника читает XLSX — нужен только
разработчику, не в рантайме):

```toml
    "openpyxl>=3.1.5",
```

- [ ] **Step 2: Синхронизировать окружение**

Run: `uv sync`
Expected: установка проходит, в выводе присутствуют `rapidfuzz` и `pillow-heif`.

- [ ] **Step 3: Проверить импорт**

Run: `uv run python -c "import rapidfuzz, pillow_heif; print(rapidfuzz.__version__)"`
Expected: печатает `3.14.5` без ошибок.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(botkin): добавить rapidfuzz и pillow-heif"
```

---

## Task 2: Конфигурация — новые константы и фиксы

**Files:**
- Modify: `src/botkin/config.py`
- Test: `tests/test_smoke.py` (расширяем существующий `test_config_imports`)

- [ ] **Step 1: Дополнить тест конфигурации**

В `tests/test_smoke.py` заменить `test_config_imports` на:

```python
def test_config_imports():
    from botkin.config import (
        VLM_MODEL, VLM_TEMPERATURE, VLM_NUM_CTX, VLM_MAX_TOKENS,
        VLM_NUM_PREDICT, VLM_REPEAT_PENALTY, OLLAMA_KEEP_ALIVE,
        PDF_RENDER_DPI, MAX_PAGES,
        IMAGE_MAX_LONG_SIDE, IMAGE_JPEG_QUALITY, IMAGE_CLASSIFY_LONG_SIDE,
        DRUG_MAX_EDIT_RATIO, DRUG_RATIO_FLOOR,
        SQLITE_PATH, UPLOAD_MAX_BYTES, UPLOAD_ALLOWED_EXTENSIONS,
    )
    assert VLM_MODEL == "qwen3-vl:8b-instruct"  # instruct-вариант, не thinking
    assert 0.0 <= VLM_TEMPERATURE <= 1.0
    assert VLM_NUM_CTX > 0
    assert VLM_MAX_TOKENS > 0
    assert VLM_NUM_PREDICT > 0
    assert VLM_REPEAT_PENALTY > 0
    assert isinstance(OLLAMA_KEEP_ALIVE, str) and len(OLLAMA_KEEP_ALIVE) > 0
    assert PDF_RENDER_DPI > 0
    assert MAX_PAGES > 0
    assert IMAGE_MAX_LONG_SIDE > IMAGE_CLASSIFY_LONG_SIDE > 0
    assert 1 <= IMAGE_JPEG_QUALITY <= 100
    assert 0 < DRUG_MAX_EDIT_RATIO < 1
    assert 0 < DRUG_RATIO_FLOOR <= 100
    assert len(SQLITE_PATH) > 0
    assert UPLOAD_MAX_BYTES > 0
    assert len(UPLOAD_ALLOWED_EXTENSIONS) > 0
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_smoke.py::test_config_imports -v`
Expected: FAIL (ImportError: cannot import name `VLM_NUM_PREDICT`).

- [ ] **Step 3: Обновить `config.py`**

В `_DEFAULTS["vlm"]` добавить `"num_predict": 8192`. В `_DEFAULTS` добавить ключи `image`
и `ollama`, и заменить блок `pdf_to_image` на DPI-ориентированный:

```python
_DEFAULTS: dict = {
    "vlm": {
        "model": "qwen3-vl:8b-instruct",
        "temperature": 0.0,
        "num_ctx": 16384,
        "max_tokens": 8192,
        "num_predict": 8192,
        "repeat_penalty": 1.2,
    },
    "ollama": {
        "keep_alive": "30m",
    },
    "pdf_to_image": {
        "render_dpi": 200,
        "max_pages": 50,
    },
    "image": {
        "max_long_side": 1800,
        "jpeg_quality": 90,
        "classify_long_side": 1000,
    },
    "database": {
        "sqlite_path": "./data/botkin.db",
    },
    "bot": {
        "polling_timeout": 30,
        "api_url": "http://localhost:8000",
    },
    "upload": {
        "max_bytes": 20 * 1024 * 1024,
        "allowed_extensions": [".pdf", ".jpg", ".jpeg", ".png", ".heic", ".webp"],
        "sources_dir": "./sources",
    },
    "drugs": {
        "max_edit_ratio": 0.40,
        "ratio_floor": 70,
    },
}
```

Заменить секции `VLM`, `PDF → изображение` и добавить новые так:

```python
# ── VLM ──────────────────────────────────────────────────────────────────────
VLM_MODEL = os.getenv("VLM_MODEL", _get("vlm.model", _DEFAULTS["vlm"]["model"]))
VLM_TEMPERATURE = float(os.getenv("VLM_TEMPERATURE", _get("vlm.temperature", _DEFAULTS["vlm"]["temperature"])))
VLM_NUM_CTX = int(os.getenv("VLM_NUM_CTX", _get("vlm.num_ctx", _DEFAULTS["vlm"]["num_ctx"])))
VLM_MAX_TOKENS = int(os.getenv("VLM_MAX_TOKENS", _get("vlm.max_tokens", _DEFAULTS["vlm"]["max_tokens"])))
VLM_NUM_PREDICT = int(os.getenv("VLM_NUM_PREDICT", _get("vlm.num_predict", _DEFAULTS["vlm"]["num_predict"])))
VLM_REPEAT_PENALTY = float(os.getenv("VLM_REPEAT_PENALTY", _get("vlm.repeat_penalty", _DEFAULTS["vlm"]["repeat_penalty"])))

# ── Ollama ────────────────────────────────────────────────────────────────────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
# keep_alive держит модель в VRAM между вызовами — нет перезагрузки весов 6 ГБ
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", _get("ollama.keep_alive", _DEFAULTS["ollama"]["keep_alive"]))

# ── PDF → изображение ─────────────────────────────────────────────────────────
PDF_RENDER_DPI = int(_get("pdf_to_image.render_dpi", _DEFAULTS["pdf_to_image"]["render_dpi"]))
MAX_PAGES = int(_get("pdf_to_image.max_pages", _DEFAULTS["pdf_to_image"]["max_pages"]))

# ── Подготовка изображений ────────────────────────────────────────────────────
IMAGE_MAX_LONG_SIDE = int(_get("image.max_long_side", _DEFAULTS["image"]["max_long_side"]))
IMAGE_JPEG_QUALITY = int(_get("image.jpeg_quality", _DEFAULTS["image"]["jpeg_quality"]))
IMAGE_CLASSIFY_LONG_SIDE = int(_get("image.classify_long_side", _DEFAULTS["image"]["classify_long_side"]))

# ── Нормализация лекарств ─────────────────────────────────────────────────────
# Scorer = дистанция Дамерау-Левенштейна (выбран по замеру на словаре 11k, см. спек).
# cap = max(1, floor(len(имя) * DRUG_MAX_EDIT_RATIO)); фильтр fuzz.ratio ≥ DRUG_RATIO_FLOOR.
DRUG_MAX_EDIT_RATIO = float(_get("drugs.max_edit_ratio", _DEFAULTS["drugs"]["max_edit_ratio"]))
DRUG_RATIO_FLOOR = float(_get("drugs.ratio_floor", _DEFAULTS["drugs"]["ratio_floor"]))
```

Удалить старые строки `PDF_SCALE_X`, `PDF_SCALE_Y` (заменены на DPI).

- [ ] **Step 4: Обновить `config.json` в корне**

Привести `config.json` к новой схеме (модель → instruct, DPI вместо scale):

```json
{
  "vlm": {
    "model": "qwen3-vl:8b-instruct",
    "temperature": 0.0,
    "num_ctx": 16384,
    "max_tokens": 8192,
    "num_predict": 8192,
    "repeat_penalty": 1.2
  },
  "ollama": { "keep_alive": "30m" },
  "pdf_to_image": { "render_dpi": 200, "max_pages": 50 },
  "image": { "max_long_side": 1800, "jpeg_quality": 90, "classify_long_side": 1000 },
  "database": { "sqlite_path": "./data/botkin.db" },
  "bot": { "polling_timeout": 30, "api_url": "http://localhost:8000" },
  "upload": {
    "max_bytes": 20971520,
    "allowed_extensions": [".pdf", ".jpg", ".jpeg", ".png", ".heic", ".webp"],
    "sources_dir": "./sources"
  },
  "drugs": { "max_edit_ratio": 0.40, "ratio_floor": 70 }
}
```

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `uv run pytest tests/test_smoke.py::test_config_imports -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/botkin/config.py config.json tests/test_smoke.py
git commit -m "feat(config): instruct-модель, DPI, keep_alive, пороги изображений и фаззи; фикс num_predict/repeat_penalty"
```

---

## Task 3: `normalize/numbers.py` — парсинг чисел

**Files:**
- Create: `src/botkin/normalize/__init__.py` (пустой)
- Create: `src/botkin/normalize/numbers.py`
- Test: `tests/test_normalize_numbers.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_normalize_numbers.py
import pytest
from botkin.normalize.numbers import parse_decimal


@pytest.mark.parametrize("raw, expected_value", [
    ("4,5", 4.5),          # десятичная запятая
    ("4.5", 4.5),
    ("145", 145.0),
    ("12,3 г/л", 12.3),    # с мусором
    ("  7,0  ", 7.0),
    ("1 234,5", 1234.5),   # пробел-разделитель тысяч
])
def test_parse_decimal_values(raw, expected_value):
    value, raw_out = parse_decimal(raw)
    assert value == expected_value
    assert raw_out == raw

def test_parse_decimal_passthrough_number():
    assert parse_decimal(4.5) == (4.5, None)
    assert parse_decimal(3) == (3.0, None)

def test_parse_decimal_none_and_garbage():
    assert parse_decimal(None) == (None, None)
    assert parse_decimal("не обнаружено") == (None, "не обнаружено")
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_normalize_numbers.py -v`
Expected: FAIL (ModuleNotFoundError: `botkin.normalize.numbers`).

- [ ] **Step 3: Реализовать**

```python
# src/botkin/normalize/numbers.py
"""Нормализация числовых значений: десятичная запятая → точка."""
from __future__ import annotations

import re

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def parse_decimal(raw: str | int | float | None) -> tuple[float | None, str | None]:
    """Парсит число из значения.

    Возвращает (нормализованное_число | None, сырая_строка | None).
    Сырая строка возвращается только для текстового входа (для хранения оригинала).
    """
    if raw is None:
        return (None, None)
    if isinstance(raw, (int, float)):
        return (float(raw), None)

    raw_out = str(raw)
    # Убираем разделители тысяч (пробел/NBSP) и приводим запятую к точке.
    cleaned = raw_out.replace(" ", "").replace(" ", "").replace(",", ".")
    match = _NUMBER_RE.search(cleaned)
    if not match:
        return (None, raw_out)
    try:
        return (float(match.group()), raw_out)
    except ValueError:
        return (None, raw_out)
```

Создать пустой `src/botkin/normalize/__init__.py`.

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_normalize_numbers.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add src/botkin/normalize/__init__.py src/botkin/normalize/numbers.py tests/test_normalize_numbers.py
git commit -m "feat(normalize): парсинг чисел с десятичной запятой"
```

---

## Task 4: `normalize/dates.py` — парсинг дат

**Files:**
- Create: `src/botkin/normalize/dates.py`
- Modify: `src/botkin/domain/models.py` (делегировать `parse_ru_date`)
- Test: `tests/test_normalize_dates.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_normalize_dates.py
from datetime import datetime
import pytest
from botkin.normalize.dates import parse_date


@pytest.mark.parametrize("raw, expected", [
    ("23 марта 2026 г.", datetime(2026, 3, 23)),
    ("23.03.2026", datetime(2026, 3, 23)),
    ("23/03/2026", datetime(2026, 3, 23)),
    ("23-03-2026", datetime(2026, 3, 23)),
    ("23.03.26", datetime(2026, 3, 23)),
    ("2026-03-23", datetime(2026, 3, 23)),
    ("2026-03-23T10:30:00", datetime(2026, 3, 23, 10, 30, 0)),
])
def test_parse_date_formats(raw, expected):
    dt, raw_out = parse_date(raw)
    assert dt == expected
    assert raw_out == raw

def test_parse_date_passthrough_datetime():
    dt = datetime(2026, 1, 1)
    assert parse_date(dt) == (dt, None)

def test_parse_date_none_and_garbage():
    assert parse_date(None) == (None, None)
    value, raw_out = parse_date("дата не указана")
    assert value is None
    assert raw_out == "дата не указана"
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_normalize_dates.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Реализовать**

```python
# src/botkin/normalize/dates.py
"""Нормализация дат из разных форматов к единому datetime (ISO)."""
from __future__ import annotations

from datetime import datetime

_MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

# Числовые форматы в порядке приоритета. %y покрывает двузначный год.
_NUMERIC_FORMATS = (
    "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y",
    "%d.%m.%y", "%d/%m/%y", "%d-%m-%y",
    "%Y-%m-%d",
)


def parse_date(value: str | datetime | None) -> tuple[datetime | None, str | None]:
    """Парсит дату из строки множества форматов.

    Возвращает (datetime | None, сырая_строка | None). Сырая строка возвращается
    только для текстового входа (для хранения оригинала из документа).
    """
    if value is None or isinstance(value, datetime):
        return (value, None)
    if not isinstance(value, str):
        return (None, None)

    raw_out = value
    cleaned = value.strip().lower().replace(" г.", "").replace("г.", "").strip()

    # 1. Русский месяц прописью: "23 марта 2026"
    parts = cleaned.split()
    if len(parts) == 3 and parts[1] in _MONTHS_RU:
        try:
            day, month_name, year = parts
            return (datetime(int(year), _MONTHS_RU[month_name], int(day)), raw_out)
        except (ValueError, KeyError):
            pass

    # 2. ISO с временем
    try:
        return (datetime.fromisoformat(cleaned.replace("z", "+00:00")), raw_out)
    except ValueError:
        pass

    # 3. Числовые форматы
    for fmt in _NUMERIC_FORMATS:
        try:
            return (datetime.strptime(cleaned, fmt), raw_out)
        except ValueError:
            continue

    return (None, raw_out)
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_normalize_dates.py -v`
Expected: PASS.

- [ ] **Step 5: Делегировать `parse_ru_date` в domain/models**

В `src/botkin/domain/models.py` удалить локальный словарь `_MONTHS_RU` и тело `parse_ru_date`,
заменить на тонкую обёртку (сохраняет существующий контракт валидаторов — возвращает только
`datetime | None`):

```python
from botkin.normalize.dates import parse_date as _parse_date


def parse_ru_date(value: "str | datetime | None") -> "datetime | None":
    """Совместимость: возвращает только datetime (сырое хранит orchestrator)."""
    dt, _ = _parse_date(value)
    return dt
```

Убрать ставшие неиспользуемыми импорты (если `Literal`/прочее ещё нужны — оставить).

- [ ] **Step 6: Прогнать связанные тесты**

Run: `uv run pytest tests/test_smoke.py::test_domain_models tests/test_normalize_dates.py -v`
Expected: PASS (включая существующий `parse_ru_date("23 марта 2026 г.")`).

- [ ] **Step 7: Commit**

```bash
git add src/botkin/normalize/dates.py src/botkin/domain/models.py tests/test_normalize_dates.py
git commit -m "feat(normalize): устойчивый парсер дат; parse_ru_date делегирует в normalize.dates"
```

---

## Task 5: `normalize/units.py` — канонизация единиц

**Files:**
- Create: `src/botkin/reference/__init__.py` (пустой)
- Create: `src/botkin/reference/units.py`
- Create: `src/botkin/normalize/units.py`
- Test: `tests/test_normalize_units.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_normalize_units.py
from botkin.normalize.units import canonical_unit


def test_canonical_known_variants():
    assert canonical_unit("10^9/L")[0] == "×10⁹/л"
    assert canonical_unit("×10^9/л")[0] == "×10⁹/л"
    assert canonical_unit("тыс/мкл")[0] == "×10⁹/л"
    assert canonical_unit("g/l")[0] == "г/л"

def test_canonical_preserves_raw():
    canon, raw = canonical_unit("10^9/L")
    assert raw == "10^9/L"

def test_canonical_unknown_passthrough():
    canon, raw = canonical_unit("ммоль/л")
    assert canon == "ммоль/л"   # неизвестное остаётся как есть
    assert raw == "ммоль/л"

def test_canonical_none():
    assert canonical_unit(None) == (None, None)
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_normalize_units.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Реализовать таблицу и функцию**

```python
# src/botkin/reference/units.py
"""Таблица соответствий единиц измерения → каноничная форма.

Ключи нормализованы (lower, без пробелов). Значения — каноничное отображение.
Список намеренно небольшой и расширяемый: покрываем частые варианты из лаб-бланков.
"""

UNIT_ALIASES: dict[str, str] = {
    "10^9/l": "×10⁹/л",
    "×10^9/л": "×10⁹/л",
    "x10^9/л": "×10⁹/л",
    "10*9/л": "×10⁹/л",
    "тыс/мкл": "×10⁹/л",
    "10^12/l": "×10¹²/л",
    "×10^12/л": "×10¹²/л",
    "млн/мкл": "×10¹²/л",
    "g/l": "г/л",
    "г/дл": "г/дл",
    "g/dl": "г/дл",
    "mmol/l": "ммоль/л",
    "umol/l": "мкмоль/л",
    "мкмоль/л": "мкмоль/л",
}
```

```python
# src/botkin/normalize/units.py
"""Канонизация единиц измерения лабораторных показателей."""
from __future__ import annotations

from botkin.reference.units import UNIT_ALIASES


def _key(raw: str) -> str:
    return raw.strip().lower().replace(" ", "")


def canonical_unit(raw: str | None) -> tuple[str | None, str | None]:
    """Возвращает (каноничная_единица | None, сырая | None).

    Неизвестные единицы возвращаются как есть (не теряем данные).
    """
    if raw is None:
        return (None, None)
    canon = UNIT_ALIASES.get(_key(raw), raw.strip())
    return (canon, raw)
```

Создать пустой `src/botkin/reference/__init__.py`.

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_normalize_units.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/botkin/reference/__init__.py src/botkin/reference/units.py src/botkin/normalize/units.py tests/test_normalize_units.py
git commit -m "feat(normalize): канонизация единиц измерения"
```

---

## Task 6: `normalize/drugs.py` — фаззи-коррекция названий

**Files:**
- Create: `src/botkin/normalize/drugs.py`
- Test: `tests/test_normalize_drugs.py`

> `src/botkin/reference/drugs/lookup.txt` (11 153 названия) уже собран и закоммичен build-скриптом
> (Task 7, выполнен). Здесь его не пересоздаём. Юнит-тесты используют небольшие списки в памяти
> + один тест читает упакованный файл.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_normalize_drugs.py
from botkin.normalize.drugs import DrugNormalizer


def _rec(name, type="trade", mnn=None, statuses=("active",), reg_numbers=()):
    return {"name": name, "type": type, "mnn": mnn,
            "statuses": list(statuses), "reg_numbers": list(reg_numbers)}


def _norm(records=None):
    # Небольшой набор записей в памяти — не тянем полный справочник в юнит-тест.
    records = records or [
        _rec("Элькар", mnn="Левокарнитин", statuses=["modified"], reg_numbers=["ЛСР-006143/10"]),
        _rec("Глиатилин", mnn="Холина альфосцерат", statuses=["active", "eaeu"]),
        _rec("Флуоксетин", type="both", mnn="Флуоксетин", statuses=["active", "excluded"]),
        _rec("Триттико", mnn="Тразодон", statuses=["eaeu"]),
        _rec("Аторвастатин", type="mnn"),
    ]
    return DrugNormalizer(records)   # параметры из config (Дамерау-cap + ratio-floor)


def test_corrects_misread_names():
    n = _norm()
    assert n.correct("элкап").canonical == "Элькар"        # dist=2
    assert n.correct("элкап").status == "matched"
    assert n.correct("глиалатин").canonical == "Глиатилин"  # dist=3 (транспозиция)
    assert n.correct("Флюоксетин").canonical == "Флуоксетин"
    assert n.correct("тритико").canonical == "Триттико"


def test_match_carries_mnn_and_statuses():
    m = _norm().correct("элкап")
    assert m.mnn == "Левокарнитин"                          # заполнение МНН из торгового
    assert "modified" in m.statuses
    assert m.reg_numbers == ("ЛСР-006143/10",)


def test_exact_match_zero_distance():
    m = _norm().correct("аторвастатин")
    assert m.canonical == "Аторвастатин" and m.distance == 0


def test_preserves_raw_always():
    assert _norm().correct("Элкап").raw == "Элкап"          # оригинал не теряется


def test_unknown_drug_not_snapped():
    match = _norm().correct("ксенобластомицинпрепарат")
    assert match.status == "unverified"
    assert match.canonical is None
    assert match.raw == "ксенобластомицинпрепарат"


def test_ratio_floor_rejects_within_cap_but_dissimilar():
    n = _norm([_rec("Парацетамол", type="mnn")])
    assert n.correct("кофе").status == "unverified"


def test_free_text_strips_dose_tail():
    # doctor_report.medications — строка с дозой/формой.
    m = _norm().correct_free_text("Элкап - 300 мг/мл (питьевая форма) по 2,5 мл")
    assert m.canonical == "Элькар"


def test_loader_reads_packaged_registry():
    from botkin.normalize.drugs import load_default
    n = load_default()
    # В registry.jsonl (ГРЛС) есть «Элькар»/«Глиатилин» — мисриды чинятся при дефолтном config.
    assert n.correct("элкап").canonical == "Элькар"
    assert n.correct("Глиалатин").canonical == "Глиатилин"
    assert n.correct("элкап").mnn == "Левокарнитин"
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_normalize_drugs.py -v`
Expected: FAIL (ModuleNotFoundError: `botkin.normalize.drugs`).

- [ ] **Step 3: Реализовать**

```python
# src/botkin/normalize/drugs.py
"""Фаззи-коррекция названий лекарств по структурному справочнику ГРЛС (registry.jsonl).

Scorer выбран по замеру на словаре 20 948 названий (см. спек): абсолютная дистанция
Дамерау-Левенштейна ставит верный ответ первым (OCR-ошибки = расстояние 1–3), тогда как WRatio
и JaroWinkler на большом словаре дают ложные совпадения.

Правило безопасности: если совпадение не проходит порог (cap по расстоянию ИЛИ ratio-floor),
название НЕ подменяется — оригинал сохраняется, статус 'unverified' (защита редких препаратов).

На matched возвращается запись справочника: каноничное имя, тип, связанный МНН (для торговых —
позволяет заполнить МНН), статусы-списки (для подсветки «исключён»/«приостановлено») и рег-номера.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rapidfuzz import distance, fuzz, process

from botkin.config import DRUG_MAX_EDIT_RATIO, DRUG_RATIO_FLOOR

_REGISTRY_PATH = Path(__file__).parent.parent / "reference" / "drugs" / "registry.jsonl"
# Хвост свободного текста: всё с первой цифры или разделителя дозы/формы.
_DOSE_TAIL_RE = re.compile(r"[-–—,(]|\d")


@dataclass(frozen=True)
class DrugMatch:
    """Результат сверки названия со справочником."""
    raw: str                          # что прочла модель (всегда сохраняется)
    canonical: str | None             # каноничное название или None
    type: str | None                  # "trade" | "mnn" | "both"
    mnn: str | None                   # связанное МНН (для торговых)
    statuses: tuple[str, ...]         # списки-статусы из реестра
    reg_numbers: tuple[str, ...]      # номера РУ (для торговых)
    status: str                       # "matched" | "unverified"
    distance: int | None              # расстояние Дамерау-Левенштейна
    ratio: float                      # fuzz.ratio к кандидату (0–100)


def _normalize_name(name: str) -> str:
    """lower, ё→е, схлопывание пробелов — для устойчивого матчинга."""
    return " ".join(name.strip().lower().replace("ё", "е").split())


def _unverified(raw: str, dist: int | None = None, ratio: float = 0.0) -> DrugMatch:
    return DrugMatch(raw=raw, canonical=None, type=None, mnn=None, statuses=(),
                     reg_numbers=(), status="unverified", distance=dist, ratio=ratio)


class DrugNormalizer:
    """Сверяет распознанные названия лекарств со структурным справочником через RapidFuzz."""

    def __init__(
        self,
        records: Iterable[dict],
        max_edit_ratio: float = DRUG_MAX_EDIT_RATIO,
        ratio_floor: float = DRUG_RATIO_FLOOR,
    ):
        self._max_edit_ratio = max_edit_ratio
        self._ratio_floor = ratio_floor
        # Карта: нормализованное имя → запись справочника.
        self._by_key: dict[str, dict] = {}
        for record in records:
            key = _normalize_name(record["name"])
            if key and key not in self._by_key:
                self._by_key[key] = record
        self._choices: list[str] = list(self._by_key)

    def correct(self, raw_name: str) -> DrugMatch:
        query = _normalize_name(raw_name)
        if not query or not self._choices:
            return _unverified(raw_name)

        # Лимит правок зависит от длины: короткие имена строже (меньше ложных снапов).
        cap = max(1, math.floor(len(query) * self._max_edit_ratio))
        best = process.extractOne(
            query, self._choices,
            scorer=distance.DamerauLevenshtein.distance,
            score_cutoff=cap,   # для distance-scorer это МАКСимально допустимое расстояние
        )
        if best is None:
            return _unverified(raw_name)

        matched_key, dist, _ = best
        ratio = fuzz.ratio(query, matched_key)
        if ratio < self._ratio_floor:
            return _unverified(raw_name, dist=int(dist), ratio=ratio)

        record = self._by_key[matched_key]
        return DrugMatch(
            raw=raw_name,
            canonical=record["name"],
            type=record.get("type"),
            mnn=record.get("mnn"),
            statuses=tuple(record.get("statuses", ())),
            reg_numbers=tuple(record.get("reg_numbers", ())),
            status="matched",
            distance=int(dist),
            ratio=ratio,
        )

    def correct_free_text(self, line: str) -> DrugMatch:
        """Best-effort для строк с дозой/формой (doctor_report.medications).

        Отрезает хвост с первой цифры/разделителя, берёт ведущее имя, при неудаче — первое слово.
        Оригинальная строка сохраняется как raw.
        """
        head = _DOSE_TAIL_RE.split(line, maxsplit=1)[0].strip()
        if not head:
            return _unverified(line)
        match = self.correct(head)
        if match.status == "unverified" and " " in head:
            match = self.correct(head.split()[0])
        # raw всегда = исходная строка целиком
        return DrugMatch(
            raw=line, canonical=match.canonical, type=match.type, mnn=match.mnn,
            statuses=match.statuses, reg_numbers=match.reg_numbers,
            status=match.status, distance=match.distance, ratio=match.ratio,
        )


def _read_registry(path: Path = _REGISTRY_PATH) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if "_meta" in obj:   # первая строка — метаданные источника
            continue
        records.append(obj)
    return records


def load_default() -> DrugNormalizer:
    """Создаёт нормализатор из упакованного registry.jsonl и параметров из config."""
    return DrugNormalizer(_read_registry())
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_normalize_drugs.py -v`
Expected: PASS.

> Параметры `DRUG_MAX_EDIT_RATIO=0.40` / `DRUG_RATIO_FLOOR=70` в config — стартовые, проверены
> на словаре 11k (13/13 золотых кейсов). Тонкая настройка — на реальных данных пользователя.

- [ ] **Step 5: Commit**

```bash
git add src/botkin/normalize/drugs.py tests/test_normalize_drugs.py
git commit -m "feat(normalize): фаззи-коррекция лекарств (Дамерау-cap + ratio-floor, «не уверен — не подменяй»)"
```

---

## Task 7: `scripts/build_drug_reference.py` — сборка справочника

**Files:**
- Create: `scripts/build_drug_reference.py`
- Test: `tests/test_build_drug_reference.py`

> **СТАТУС: уже реализовано и выполнено.** `scripts/build_drug_reference.py` (+`scripts/__init__.py`)
> написан и запущен на вашей выгрузке `grls2026-06-02-1.zip`; собранный
> `src/botkin/reference/drugs/registry.jsonl` (20 948 названий) закоммичен. Источник — официальная
> ZIP-выгрузка ГРЛС (8 листов-статусов), парсинг openpyxl. Сам ZIP (~18 МБ) — вход скрипта, в репо
> не кладётся. Ниже — офлайн-тесты (собирают мини-XLSX в памяти, без сети).

- [ ] **Step 1: Написать падающий тест (офлайн)**

```python
# tests/test_build_drug_reference.py
import io
import zipfile

import openpyxl

from scripts.build_drug_reference import (
    build_registry, is_meaningful_name, normalize_key, status_of,
)


def test_status_of_maps_sheet_titles():
    assert status_of("Действующий") == "active"
    assert status_of("Исключённый") == "excluded"
    assert status_of("Приостановлено применение") == "suspended"


def test_normalize_key_and_meaningful():
    assert normalize_key(" Глиатилин ") == "глиатилин"
    assert normalize_key("Тёма") == "тема"            # ё→е
    assert is_meaningful_name("Элькар")
    assert not is_meaningful_name("12")               # нет кириллицы
    assert not is_meaningful_name("ок")               # короче 3


def _make_grls_zip(tmp_path, rows, sheet_title="Действующий"):
    """Мини-XLSX в формате ГРЛС: шапка в первых 6 строках, данные с 7-й (индекс 6)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title
    for _ in range(6):
        ws.append([None] * 17)
    for reg, trade, mnn in rows:
        row = [None] * 17
        row[2], row[8], row[9] = reg, trade, mnn
        ws.append(row)
    xlsx = io.BytesIO()
    wb.save(xlsx)
    path = tmp_path / "grls.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("grls-Действующий.xlsx", xlsx.getvalue())
    return path


def test_build_registry_extracts_structured_record(tmp_path):
    path = _make_grls_zip(tmp_path, [("ЛП-1", "Глиатилин", "Холина альфосцерат")])
    registry = build_registry(path)

    trade = registry["глиатилин"]
    assert trade["types"] == {"trade"}
    assert trade["name"] == "Глиатилин"
    assert trade["mnn"] == "Холина альфосцерат"
    assert trade["statuses"] == {"active"}
    assert trade["reg_numbers"] == {"ЛП-1"}
    # МНН индексируется отдельной записью
    assert "холина альфосцерат" in registry


def test_build_registry_skips_rows_without_reg_or_name(tmp_path):
    path = _make_grls_zip(tmp_path, [(None, "Глиатилин", "X"), ("ЛП-2", None, "Y")])
    assert build_registry(path) == {}
```

- [ ] **Step 2: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_build_drug_reference.py -v`
Expected: PASS (офлайн; файл скрипта уже в репозитории).

- [ ] **Step 3: Реализация (уже в репозитории)**

`scripts/build_drug_reference.py` написан и закоммичен. Ключевые функции:

- `status_of(sheet_title) -> str` — имя листа → код статуса (`active`/`excluded`/`suspended`/…).
- `normalize_key(name) -> str` / `is_meaningful_name(name) -> bool` — ключ матчинга и фильтр шума.
- `build_registry(zip_path) -> dict[key, record]` — читает все 8 XLSX (openpyxl, колонки
  рег-номер=2, торговое=8, МНН=9; данные с 7-й строки), агрегирует по нормализованному имени:
  `types`, `mnn`-связку, `statuses`, `reg_numbers`.
- `write_registry(registry, out_path, source_note)` — пишет `registry.jsonl` (первая строка —
  `_meta` с источником; далее по записи на имя, `_record_to_json` сворачивает `types`→`type`).
- `main()` (`pragma: no cover`) — `--src <zip> --out registry.jsonl`.

> Запуск пользователем при обновлении выгрузки (сеть не нужна):
> `uv run python -m scripts.build_drug_reference --src grls2026-06-02-1.zip --out src/botkin/reference/drugs/registry.jsonl`

- [ ] **Step 4: Commit**

```bash
git add tests/test_build_drug_reference.py
git commit -m "test(scripts): офлайн-тесты разбора ГРЛС-ZIP и агрегации статусов/рег-номеров"
```

---

## Task 8: `preprocess/images.py` — подготовка изображений

**Files:**
- Create: `src/botkin/preprocess/__init__.py` (пустой)
- Create: `src/botkin/preprocess/images.py`
- Test: `tests/test_preprocess_images.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_preprocess_images.py
import io
import pymupdf
import pytest
from PIL import Image

from botkin.preprocess.images import prepare_images, to_base64_jpegs


def _make_pdf(tmp_path, pages=2):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i} Гемоглобин 145 г/л")
    path = tmp_path / "doc.pdf"
    doc.save(str(path)); doc.close()
    return path


def _make_png(tmp_path, size=(4000, 3000)):
    img = Image.new("RGB", size, (255, 255, 255))
    path = tmp_path / "photo.png"
    img.save(str(path))
    return path


def test_pdf_yields_one_image_per_page(tmp_path):
    path = _make_pdf(tmp_path, pages=2)
    images = prepare_images(path)
    assert len(images) == 2
    for raw in images:
        Image.open(io.BytesIO(raw))   # валидный JPEG, не бросает


def test_large_photo_downscaled(tmp_path):
    path = _make_png(tmp_path, size=(4000, 3000))
    images = prepare_images(path)
    assert len(images) == 1
    img = Image.open(io.BytesIO(images[0]))
    assert max(img.size) <= 1800   # IMAGE_MAX_LONG_SIDE по умолчанию


def test_classify_variant_smaller(tmp_path):
    path = _make_png(tmp_path, size=(4000, 3000))
    images = prepare_images(path, long_side=1000)
    img = Image.open(io.BytesIO(images[0]))
    assert max(img.size) <= 1000


def test_to_base64(tmp_path):
    path = _make_png(tmp_path, size=(800, 600))
    b64 = to_base64_jpegs(prepare_images(path))
    assert isinstance(b64, list) and b64 and isinstance(b64[0], str)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        prepare_images(tmp_path / "nope.pdf")
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_preprocess_images.py -v`
Expected: FAIL (ModuleNotFoundError: `botkin.preprocess.images`).

- [ ] **Step 3: Реализовать**

```python
# src/botkin/preprocess/images.py
"""Подготовка PDF/изображений к VLM: контролируемое разрешение и JPEG-качество.

Размер изображения напрямую определяет число vision-токенов и латентность,
поэтому длинная сторона приводится к разумному пределу (см. config.IMAGE_MAX_LONG_SIDE).
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

# Регистрируем HEIC-плагин для Pillow (фото с iPhone).
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover — окружение без pillow-heif
    pass

from botkin.config import (
    IMAGE_JPEG_QUALITY,
    IMAGE_MAX_LONG_SIDE,
    MAX_PAGES,
    PDF_RENDER_DPI,
)


def _downscale(img: Image.Image, long_side: int) -> Image.Image:
    img = ImageOps.exif_transpose(img)   # учёт ориентации из EXIF (фото с телефона)
    if img.mode != "RGB":
        img = img.convert("RGB")
    width, height = img.size
    longest = max(width, height)
    if longest > long_side:
        ratio = long_side / longest
        img = img.resize((round(width * ratio), round(height * ratio)), Image.LANCZOS)
    return img


def _encode_jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=IMAGE_JPEG_QUALITY)
    return buf.getvalue()


def _pdf_pages(path: Path, long_side: int) -> list[bytes]:
    out: list[bytes] = []
    doc = pymupdf.open(str(path))
    try:
        for index, page in enumerate(doc):
            if index >= MAX_PAGES:
                break
            pix = page.get_pixmap(dpi=PDF_RENDER_DPI)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            out.append(_encode_jpeg(_downscale(img, long_side)))
    finally:
        doc.close()
    return out


def prepare_images(file_path: Path | str, long_side: int | None = None) -> list[bytes]:
    """PDF/изображение → список JPEG-байтов с контролируемым разрешением.

    long_side по умолчанию = IMAGE_MAX_LONG_SIDE; для дешёвой классификации передаётся
    меньшее значение (IMAGE_CLASSIFY_LONG_SIDE).
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")

    limit = long_side or IMAGE_MAX_LONG_SIDE
    if path.suffix.lower() == ".pdf":
        return _pdf_pages(path, limit)

    with Image.open(path) as img:
        return [_encode_jpeg(_downscale(img, limit))]


def to_base64_jpegs(images: list[bytes]) -> list[str]:
    """Кодирует JPEG-байты в base64-строки для data-url."""
    return [base64.b64encode(raw).decode("utf-8") for raw in images]
```

Создать пустой `src/botkin/preprocess/__init__.py`.

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_preprocess_images.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/botkin/preprocess/__init__.py src/botkin/preprocess/images.py tests/test_preprocess_images.py
git commit -m "feat(preprocess): подготовка PDF/фото к VLM (DPI, даунскейл, EXIF, HEIC)"
```

---

## Task 9: `llm/client.py` — keep_alive

**Files:**
- Modify: `src/botkin/llm/client.py`
- Test: `tests/test_llm_calls.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_llm_calls.py
def test_keep_alive_exported():
    from botkin.llm.client import default_options
    opts = default_options()
    assert "keep_alive" in opts
    assert "num_ctx" in opts and "repeat_penalty" in opts
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_llm_calls.py::test_keep_alive_exported -v`
Expected: FAIL (ImportError: cannot import name `default_options`).

- [ ] **Step 3: Реализовать**

В `src/botkin/llm/client.py` добавить импорт и функцию-хелпер опций (единая точка
формирования `extra_body.options` для всех VLM-вызовов):

```python
from botkin.config import (
    OLLAMA_URL, OLLAMA_KEEP_ALIVE, VLM_NUM_CTX, VLM_REPEAT_PENALTY, VLM_NUM_PREDICT,
)


def default_options() -> dict:
    """Опции Ollama для VLM-вызовов. keep_alive держит модель в VRAM между вызовами."""
    return {
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "num_ctx": VLM_NUM_CTX,
        "repeat_penalty": VLM_REPEAT_PENALTY,
        "num_predict": VLM_NUM_PREDICT,
    }
```

(Существующий импорт `from botkin.config import OLLAMA_URL` заменить на расширенный выше.)

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_llm_calls.py::test_keep_alive_exported -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/botkin/llm/client.py tests/test_llm_calls.py
git commit -m "feat(llm): единые опции Ollama с keep_alive"
```

---

## Task 10: `llm/prompts.py` — убрать анти-thinking костыли

**Files:**
- Modify: `src/botkin/llm/prompts.py`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_prompts.py
from botkin.llm import prompts


def test_prompts_have_no_antithinking_cruft():
    all_text = " ".join(
        getattr(prompts, name) for name in dir(prompts)
        if name.endswith("_SYSTEM") and isinstance(getattr(prompts, name), str)
    )
    # instruct-вариант не уходит в thinking — костыли не нужны.
    assert "thinking" not in all_text.lower()
    assert "размышлени" not in all_text.lower()
    assert "```json" not in all_text   # structured output обеспечивает instructor

def test_core_prompts_present():
    assert prompts.CLASSIFY_VLM_SYSTEM
    assert prompts.ANALYSIS_VLM_SYSTEM
    assert prompts.PRESCRIPTION_VLM_SYSTEM
    assert prompts.DOCTOR_REPORT_VLM_SYSTEM
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: FAIL (находит «thinking»/«размышлени»/```json в текущих промптах).

- [ ] **Step 3: Переписать `prompts.py`**

Заменить содержимое `src/botkin/llm/prompts.py` (убраны требования к Markdown-блокам и
запреты на «размышления» — за формат отвечает `instructor`; правила извлечения сохранены):

```python
"""Промпты для классификации и извлечения данных через VLM (qwen3-vl:8b-instruct)."""

CLASSIFY_VLM_SYSTEM = """Ты — точный классификатор медицинских документов. Определи тип документа по изображению.

Типы (выбери ОДИН):
- analysis: лабораторный анализ (кровь, моча, биохимия) с показателями и нормами
- prescription: рецепт или назначение лекарств
- doctor_report: заключение врача, выписка, осмотр
- certificate: медицинская справка
- unknown: не подходит ни под один из выше

Верни doc_type и confidence (0.0–1.0)."""

ANALYSIS_VLM_SYSTEM = """Ты — медицинский ассистент, который ТОЧНО извлекает показатели из лабораторных анализов.

Правила:
1. Извлекай только реальные показатели с числовыми значениями. Заголовки таблиц пропускай.
2. Единицы сохраняй как в документе: "г/л", "ммоль/л", "%", "×10⁹/л".
3. Диапазон в ячейке ("4.0-5.5") — это референс: ref_low=4.0, ref_high=5.5.
4. analyte_name — на русском как в документе; analyte_code — на английском (HGB, RBC, GLU), если узнаёшь.
5. value_num — только число. Текстовое значение ("не обнаружено", "+", "++") → в value_text, value_num=null.
6. Сохраняй десятичные разделители как в оригинале и флаги «*», «↑», «↓», «(+)».
7. taken_at — дата забора из шапки документа, повторяй для каждой строки.
8. Отсутствующее поле — null."""

PRESCRIPTION_VLM_SYSTEM = """Ты — медицинский ассистент, который извлекает назначения лекарств из фото/сканов.

Правила:
1. Для каждого препарата извлеки МНН и торговое название.
2. МНН — на русском в нижнем регистре ("аторвастатин").
3. dose — как в документе ("10 мг", "1 таб"); frequency — кратность ("1 раз в день", "на ночь").
4. duration_days — длительность в днях ("10 дней" → 10, "2 недели" → 14).
5. Отсутствующее поле — null."""

DOCTOR_REPORT_VLM_SYSTEM = """Ты — медицинский ассистент, который извлекает заключения врача из фото/сканов.

Правила:
1. diagnosis — основной диагноз (строка).
2. complaints — список жалоб (массив строк).
3. anamnesis — анамнез (строка).
4. recommendations — список рекомендаций (массив строк).
5. medications — список назначенных лекарств (массив строк).
6. visit_date — дата приёма.
7. doctor_name — ФИО врача; department — отделение.
8. Отсутствующее поле — null или пустой список."""
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/botkin/llm/prompts.py tests/test_prompts.py
git commit -m "refactor(llm): убрать анти-thinking костыли из промптов (instruct + instructor)"
```

---

## Task 11: `llm/classify.py` и `extract.py` — препроцессинг и параметры

**Files:**
- Modify: `src/botkin/llm/classify.py`
- Modify: `src/botkin/llm/extract.py`
- Test: `tests/test_llm_calls.py` (дополняем)

- [ ] **Step 1: Написать падающий тест (с моком клиента — модель НЕ запускается)**

Добавить в `tests/test_llm_calls.py`:

```python
from unittest.mock import MagicMock, patch
import pymupdf
from botkin.domain.models import ClassifyResult


def _tiny_pdf(tmp_path):
    doc = pymupdf.open(); page = doc.new_page()
    page.insert_text((72, 72), "Гемоглобин 145 г/л")
    p = tmp_path / "a.pdf"; doc.save(str(p)); doc.close()
    return p


def test_classify_uses_small_image_and_mocked_client(tmp_path):
    from botkin.llm import classify

    fake = MagicMock()
    resp = MagicMock()
    resp.doc_type = "analysis"; resp.confidence = 0.9
    resp._raw_response.usage.prompt_tokens = 10
    resp._raw_response.usage.completion_tokens = 5
    fake.chat.completions.create.return_value = resp

    with patch("botkin.llm.classify.get_client", return_value=fake), \
         patch("botkin.llm.classify.prepare_images", return_value=[b"\xff\xd8fakejpeg"]) as prep:
        result = classify.run_vlm(_tiny_pdf(tmp_path))

    assert isinstance(result, ClassifyResult)
    assert result.doc_type == "analysis"
    # classify использует уменьшенное разрешение
    _, kwargs = prep.call_args
    from botkin.config import IMAGE_CLASSIFY_LONG_SIDE
    assert kwargs.get("long_side") == IMAGE_CLASSIFY_LONG_SIDE


def test_extract_analysis_mocked(tmp_path):
    from botkin.llm import extract
    from botkin.domain.models import LabResult

    fake = MagicMock()
    resp = MagicMock()
    resp.results = [LabResult(analyte_name="Гемоглобин", value_num=145.0, unit="г/л")]
    resp._raw_response.usage.prompt_tokens = 10
    resp._raw_response.usage.completion_tokens = 5
    resp._raw_response.choices = [MagicMock()]
    resp._raw_response.choices[0].message.content = '{"results": []}'
    fake.chat.completions.create.return_value = resp

    with patch("botkin.llm.extract.get_client", return_value=fake), \
         patch("botkin.llm.extract.prepare_images", return_value=[b"\xff\xd8fakejpeg"]):
        items = extract.run_analysis(_tiny_pdf(tmp_path))

    assert items and items[0].analyte_name == "Гемоглобин"
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_llm_calls.py -v`
Expected: FAIL (classify/extract ещё используют `_pdf_to_base64_images`, нет `prepare_images`).

- [ ] **Step 3: Переписать `classify.py`**

```python
# src/botkin/llm/classify.py
"""Классификатор типа документа через VLM (дешёвый вызов на уменьшенной 1-й странице)."""
import logging
import time
from pathlib import Path

import instructor
from pydantic import BaseModel

from botkin.config import VLM_MODEL, VLM_TEMPERATURE, VLM_MAX_TOKENS, IMAGE_CLASSIFY_LONG_SIDE
from botkin.domain.models import ClassifyResult, DocType
from botkin.exceptions import ClassificationError
from botkin.llm.client import get_client, default_options
from botkin.llm.prompts import CLASSIFY_VLM_SYSTEM
from botkin.preprocess.images import prepare_images, to_base64_jpegs

log = logging.getLogger(__name__)


class ClassifySchema(BaseModel):
    doc_type: DocType
    confidence: float


def run_vlm(source_path: Path) -> ClassifyResult:
    """Классифицирует документ по уменьшенной первой странице."""
    t0 = time.perf_counter()
    log.info("[START_CLASSIFY] Doc: '%s' | Model: %s", source_path.name, VLM_MODEL)

    images = prepare_images(source_path, long_side=IMAGE_CLASSIFY_LONG_SIDE)
    b64 = to_base64_jpegs(images[:1])   # только первая страница
    client = get_client(temperature=VLM_TEMPERATURE, mode=instructor.Mode.JSON)

    content = [
        {"type": "text", "text": "Classify this medical document image."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64[0]}"}},
    ]
    messages = [
        {"role": "system", "content": CLASSIFY_VLM_SYSTEM},
        {"role": "user", "content": content},
    ]

    try:
        response = client.chat.completions.create(
            model=VLM_MODEL,
            messages=messages,
            response_model=ClassifySchema,
            max_tokens=VLM_MAX_TOKENS,
            extra_body={"options": default_options()},
        )
        elapsed = time.perf_counter() - t0
        usage = response._raw_response.usage
        log.info(
            "[SUCCESS_CLASSIFY] Doc: '%s' | Result: '%s' (conf=%.2f) | Elapsed: %.2fs | "
            "Prompt: %d t | Completion: %d t",
            source_path.name, response.doc_type, response.confidence,
            elapsed, usage.prompt_tokens, usage.completion_tokens,
        )
        return ClassifyResult(doc_type=response.doc_type, confidence=response.confidence)
    except Exception as e:
        elapsed = time.perf_counter() - t0
        log.error("[FAILED_CLASSIFY] Doc: '%s' | Elapsed: %.2fs | Error: %s", source_path.name, elapsed, e)
        raise ClassificationError(f"Сбой классификации: {e}") from e
```

- [ ] **Step 4: Переписать `extract.py`**

Заменить блок импортов и `_pdf_to_base64_images`/`_call_vlm`/публичные функции:

```python
# src/botkin/llm/extract.py
"""VLM-извлечение структурированных данных из медицинских документов."""
import logging
import time
from pathlib import Path

import instructor
from pydantic import BaseModel

from botkin.config import VLM_MODEL, VLM_TEMPERATURE, VLM_MAX_TOKENS
from botkin.domain.models import LabResult, Prescription, DoctorReport
from botkin.exceptions import ExtractionError
from botkin.llm.client import get_client, default_options
from botkin.llm.prompts import (
    ANALYSIS_VLM_SYSTEM, PRESCRIPTION_VLM_SYSTEM, DOCTOR_REPORT_VLM_SYSTEM,
)
from botkin.preprocess.images import prepare_images, to_base64_jpegs

log = logging.getLogger(__name__)


class LabResults(BaseModel):
    results: list[LabResult] = []


class Prescriptions(BaseModel):
    results: list[Prescription] = []


class DoctorReports(BaseModel):
    results: list[DoctorReport] = []


def _build_messages(system_prompt: str, instruction: str, source_path: Path) -> list[dict]:
    b64_images = to_base64_jpegs(prepare_images(source_path))
    content: list[dict] = [{"type": "text", "text": instruction}]
    for b64 in b64_images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]


def _call_vlm(messages: list[dict], response_model: type[BaseModel], doc_name: str, doc_type: str) -> BaseModel:
    t0 = time.perf_counter()
    log.info("[START_EXTRACT] Doc: '%s' | Type: '%s' | Model: %s", doc_name, doc_type, VLM_MODEL)
    client = get_client(temperature=VLM_TEMPERATURE, mode=instructor.Mode.JSON)
    try:
        response = client.chat.completions.create(
            model=VLM_MODEL,
            messages=messages,
            response_model=response_model,
            max_retries=2,
            max_tokens=VLM_MAX_TOKENS,
            extra_body={"options": default_options()},
        )
        elapsed = time.perf_counter() - t0
        usage = response._raw_response.usage
        log.info(
            "[SUCCESS_EXTRACT] Doc: '%s' | Type: '%s' | Elapsed: %.2fs | Prompt: %d t | Completion: %d t",
            doc_name, doc_type, elapsed, usage.prompt_tokens, usage.completion_tokens,
        )
        return response
    except Exception as e:
        elapsed = time.perf_counter() - t0
        log.error("[FAILED_EXTRACT] Doc: '%s' | Type: '%s' | Elapsed: %.2fs | Error: %s", doc_name, doc_type, elapsed, e)
        raise ExtractionError(f"Сбой извлечения ({doc_type}): {e}") from e


def run_analysis(source_path: Path) -> list[LabResult]:
    messages = _build_messages(ANALYSIS_VLM_SYSTEM, "Extract lab results from these document images.", source_path)
    return _call_vlm(messages, LabResults, source_path.name, "analysis").results


def run_prescription(source_path: Path) -> list[Prescription]:
    messages = _build_messages(PRESCRIPTION_VLM_SYSTEM, "Extract prescriptions from these document images.", source_path)
    return _call_vlm(messages, Prescriptions, source_path.name, "prescription").results


def run_doctor_report(source_path: Path) -> list[DoctorReport]:
    messages = _build_messages(DOCTOR_REPORT_VLM_SYSTEM, "Extract doctor reports from these document images.", source_path)
    return _call_vlm(messages, DoctorReports, source_path.name, "doctor_report").results
```

> Удалено сохранение `.txt` рядом с источником (сырой ответ теперь хранится в БД —
> `raw_extraction`, Task 13/14). `classify.py` больше не импортирует `_pdf_to_base64_images`
> из `extract` (использует `preprocess`).

- [ ] **Step 5: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_llm_calls.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/botkin/llm/classify.py src/botkin/llm/extract.py tests/test_llm_calls.py
git commit -m "feat(llm): препроцессинг изображений + единые опции; дешёвый classify"
```

---

## Task 12: Доменные модели — `*_raw` поля

**Files:**
- Modify: `src/botkin/domain/models.py`
- Test: `tests/test_smoke.py` (дополняем `test_domain_models`)

- [ ] **Step 1: Дополнить тест**

В конец `test_domain_models` в `tests/test_smoke.py` добавить:

```python
    # *_raw поля сохраняют оригинал
    lab2 = LabResult(analyte_name="Гемоглобин", value_num=145.0, value_raw="145", unit_raw="g/l", taken_at_raw="23.03.2026")
    assert lab2.value_raw == "145"
    rx2 = Prescription(drug_mnn="аторвастатин", drug_raw="аторвастатин", match_status="matched")
    assert rx2.match_status == "matched"
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_smoke.py::test_domain_models -v`
Expected: FAIL (`LabResult` не принимает `value_raw` при `extra="forbid"`).

- [ ] **Step 3: Добавить поля в модели**

В `src/botkin/domain/models.py`:

В `LabResult` добавить:
```python
    value_raw: Optional[str] = None
    unit_raw: Optional[str] = None
    taken_at_raw: Optional[str] = None
```

В `Prescription` добавить:
```python
    drug_raw: Optional[str] = None
    match_status: Optional[str] = None
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_smoke.py::test_domain_models -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/botkin/domain/models.py tests/test_smoke.py
git commit -m "feat(domain): *_raw поля для сохранности оригинала"
```

---

## Task 13: Схема БД — миграция (ADD COLUMN)

**Files:**
- Modify: `src/botkin/db/schema.sql`
- Modify: `src/botkin/db/connection.py` (идемпотентная миграция)
- Test: `tests/test_migration.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_migration.py
def test_new_columns_exist(set_test_db):
    from botkin.db.connection import get_conn

    def cols(conn, table):
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    with get_conn() as conn:
        assert "raw_extraction" in cols(conn, "documents")
        assert {"drug_raw", "match_status", "reg_statuses", "reg_numbers"} <= cols(conn, "prescriptions")
        assert "medications_normalized_json" in cols(conn, "doctor_reports")
        assert {"value_raw", "unit_raw", "taken_at_raw"} <= cols(conn, "lab_results")


def test_migration_idempotent(set_test_db):
    # Повторный init_db не должен падать на уже добавленных колонках.
    from botkin.db.connection import init_db
    init_db()
    init_db()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_migration.py -v`
Expected: FAIL (колонок нет).

- [ ] **Step 3: Обновить `schema.sql`**

В `src/botkin/db/schema.sql`: в `CREATE TABLE documents` добавить строку перед `created_at`:
```sql
    raw_extraction TEXT,
```
В `CREATE TABLE lab_results` добавить перед `created_at`:
```sql
    value_raw TEXT,
    unit_raw TEXT,
    taken_at_raw TEXT,
```
В `CREATE TABLE prescriptions` добавить перед `created_at`:
```sql
    drug_raw TEXT,
    match_status TEXT,
    reg_statuses TEXT,
    reg_numbers TEXT,
```
В `CREATE TABLE doctor_reports` добавить перед `created_at`:
```sql
    medications_normalized_json TEXT,
```

- [ ] **Step 4: Добавить идемпотентную миграцию в `connection.py`**

Для существующих БД `CREATE TABLE IF NOT EXISTS` не добавит колонки — нужен `ALTER TABLE`.
В `src/botkin/db/connection.py` добавить и вызвать из `init_db`:

```python
# Колонки, добавляемые поверх существующих таблиц (идемпотентно).
_MIGRATIONS: dict[str, dict[str, str]] = {
    "documents": {"raw_extraction": "TEXT"},
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
```

В `init_db` после `executescript(...)` добавить вызов `_apply_migrations(conn)`:

```python
def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        _apply_migrations(conn)
```

- [ ] **Step 5: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_migration.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/botkin/db/schema.sql src/botkin/db/connection.py tests/test_migration.py
git commit -m "feat(db): raw_extraction и *_raw колонки + идемпотентная миграция"
```

---

## Task 14: Orchestrator — этап нормализации и сохранение raw

**Files:**
- Modify: `src/botkin/pipeline/orchestrator.py`
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Написать падающий тест (мок classify/extract — модель не запускается)**

```python
# tests/test_orchestrator.py
import asyncio
from unittest.mock import patch

from botkin.domain.models import Prescription, LabResult


def _make_doc(set_test_db, source_path="/tmp/x.jpg"):
    from botkin.db.connection import get_conn
    from botkin.db.repos import DocumentRepo, UserRepo
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(777)
        did = DocumentRepo(conn, uid).create(source_path=source_path)
    return uid, did


def test_prescription_drug_normalized_and_raw_saved(set_test_db):
    from botkin.pipeline import orchestrator
    from botkin.db.connection import get_conn
    from botkin.domain.models import ClassifyResult

    uid, did = _make_doc(set_test_db)

    with patch.object(orchestrator.classify, "run_vlm", return_value=ClassifyResult(doc_type="prescription", confidence=0.9)), \
         patch.object(orchestrator.extract, "run_prescription", return_value=[Prescription(drug_mnn="элкап", drug_trade="элкап")]), \
         patch("botkin.pipeline.orchestrator.notify_user", return_value=_anoop()):
        asyncio.run(orchestrator.process_document(did, 777))

    with get_conn() as conn:
        row = conn.execute(
            "SELECT drug_mnn, drug_trade, drug_raw, match_status, reg_statuses "
            "FROM prescriptions WHERE document_id=?", (did,)).fetchone()
        doc = conn.execute("SELECT status, raw_extraction FROM documents WHERE id=?", (did,)).fetchone()

    assert row["drug_raw"] == "элкап"           # оригинал сохранён
    assert row["drug_trade"] == "Элькар"        # торговое нормализовано по справочнику
    assert row["drug_mnn"] == "Левокарнитин"    # МНН дозаполнен из связки реестра
    assert row["match_status"] == "matched"
    assert "modified" in row["reg_statuses"]    # статус-список из ГРЛС сохранён
    assert doc["status"] == "extracted"
    assert doc["raw_extraction"] and "элкап" in doc["raw_extraction"]   # сырой JSON сохранён


async def _anoop(*args, **kwargs):
    return None
```

> Патч порога НЕ нужен: `get_drug_normalizer()` → `load_default()` читает реальный `registry.jsonl`
> (ГРЛС), где есть «Элькар» (МНН «Левокарнитин», статус `modified`); при дефолтных
> `DRUG_MAX_EDIT_RATIO=0.40`/`DRUG_RATIO_FLOOR=70` «элкап»→«Элькар» (dist=2) проходит как `matched`
> (проверено на реальном справочнике). Тест использует реальный нормализатор без подмены.

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: FAIL (нет нормализации/`get_drug_normalizer`/сохранения raw).

- [ ] **Step 3: Обновить `orchestrator.py`**

Добавить импорты вверху:

```python
import json

from botkin.normalize.drugs import DrugNormalizer, load_default
from botkin.normalize.dates import parse_date
from botkin.normalize.numbers import parse_decimal
from botkin.normalize.units import canonical_unit
```

Добавить ленивый синглтон нормализатора (справочник читается один раз):

```python
_DRUG_NORMALIZER: DrugNormalizer | None = None


def get_drug_normalizer() -> DrugNormalizer:
    global _DRUG_NORMALIZER
    if _DRUG_NORMALIZER is None:
        _DRUG_NORMALIZER = load_default()
    return _DRUG_NORMALIZER
```

В `_run`, после получения `items` и ПЕРЕД persist, нормализовать и сохранить сырой JSON.
Для каждой ветки доработать persist-функции. Сохранение сырого извлечения:

```python
def _save_raw_extraction(document_id: int, items: list) -> None:
    payload = json.dumps([i.model_dump(mode="json") for i in items], ensure_ascii=False)
    with get_conn() as conn:
        conn.execute("UPDATE documents SET raw_extraction = ? WHERE id = ?", (payload, document_id))
        conn.commit()
```

Вызвать `_save_raw_extraction(document_id, items)` сразу после успешного extract в каждой ветке
(analysis/prescription/doctor_report), до persist.

Нормализация назначений — заменить `_persist_prescription`:

```python
def _persist_prescription(document_id: int, user_id: int, items: list[Prescription]) -> None:
    normalizer = get_drug_normalizer()
    with get_conn() as conn:
        for item in items:
            # Сверяем по торговому названию (если есть), иначе по МНН.
            probe = item.drug_trade or item.drug_mnn
            match = normalizer.correct(probe)
            if match.status == "matched":
                # Торговое → канон; МНН дозаполняем из связки реестра (или матча типа mnn).
                drug_trade = match.canonical if match.type in ("trade", "both") else item.drug_trade
                drug_mnn = match.mnn or (match.canonical if match.type in ("mnn", "both") else item.drug_mnn)
                reg_statuses = json.dumps(list(match.statuses), ensure_ascii=False)
                reg_numbers = json.dumps(list(match.reg_numbers), ensure_ascii=False)
            else:
                drug_trade, drug_mnn, reg_statuses, reg_numbers = item.drug_trade, item.drug_mnn, None, None
            conn.execute(
                """INSERT INTO prescriptions(document_id, user_id, drug_mnn, drug_trade,
                   dose, frequency, duration_days, prescribed_at, doctor_name, form_107_1u_flag,
                   drug_raw, match_status, reg_statuses, reg_numbers)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (document_id, user_id, drug_mnn, drug_trade,
                 item.dose, item.frequency, item.duration_days,
                 item.prescribed_at.isoformat() if item.prescribed_at else None,
                 item.doctor_name, item.form_107_1u_flag,
                 probe, match.status, reg_statuses, reg_numbers),
            )
        conn.commit()
```

Нормализация свободного текста `medications` в `_persist_doctor_report` — дополнительно к
существующему сохранению JSON-полей собрать `medications_normalized_json` (список объектов
`{raw, canonical, mnn, statuses, status}`):

```python
def _normalize_medications(lines: list[str]) -> str:
    normalizer = get_drug_normalizer()
    out = []
    for line in lines:
        m = normalizer.correct_free_text(line)
        out.append({"raw": m.raw, "canonical": m.canonical, "mnn": m.mnn,
                    "statuses": list(m.statuses), "status": m.status})
    return json.dumps(out, ensure_ascii=False)
```

В `_persist_doctor_report` при INSERT добавить колонку `medications_normalized_json` со
значением `_normalize_medications(item.medications)`.

Нормализация лаб-результатов — заменить `_persist_lab` (даты/единицы/числа + сырое):

```python
def _persist_lab(document_id: int, user_id: int, items: list[LabResult]) -> None:
    with get_conn() as conn:
        for item in items:
            _, taken_raw = parse_date(item.taken_at) if isinstance(item.taken_at, str) else (item.taken_at, None)
            unit_canon, unit_raw = canonical_unit(item.unit)
            conn.execute(
                """INSERT INTO lab_results(document_id, user_id, analyte_code, analyte_name,
                   value_num, value_text, unit, ref_low, ref_high, taken_at, source_table_cell,
                   value_raw, unit_raw, taken_at_raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (document_id, user_id, item.analyte_code, item.analyte_name,
                 item.value_num, item.value_text, unit_canon,
                 item.ref_low, item.ref_high,
                 item.taken_at.isoformat() if item.taken_at else None,
                 item.source_table_cell,
                 item.value_raw, unit_raw, taken_raw),
            )
        conn.commit()
```

> `taken_at` в `LabResult` уже провалидирован в `datetime` валидатором модели; сырое
> текстовое значение сохраняем отдельно, если оно приходило строкой (`taken_at_raw`).
> `unit` приводим к канону, `unit_raw` — оригинал.

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/botkin/pipeline/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(pipeline): этап нормализации (лекарства/даты/единицы) + сохранение raw_extraction"
```

---

## Task 15: Финальная проверка всего пакета

**Files:** —

- [ ] **Step 1: Прогнать весь набор тестов**

Run: `uv run pytest -v`
Expected: все тесты проходят (smoke + новые модули). VLM не запускается нигде (везде моки).

- [ ] **Step 2: Линт**

Run: `uv run ruff check src tests scripts`
Expected: без ошибок (поправить при необходимости).

- [ ] **Step 3: Проверка импортируемости приложения (без сети/GPU)**

Run: `uv run python -c "import botkin.api.app, botkin.bot.main, botkin.pipeline.orchestrator; print('ok')"`
Expected: печатает `ok`.

- [ ] **Step 4: Финальный commit (если ruff что-то поправил)**

```bash
git add -A
git commit -m "chore(botkin): финальный прогон тестов и линта блока A+B"
```

---

## Self-review (заполнено автором плана)

**Покрытие спека:**
- G1 (скорость): Task 2 (instruct, num_ctx, keep_alive), Task 8 (препроцессинг), Task 9–11 (вызовы). Замер латентности — у пользователя (вне CI), как требует ограничение окружения.
- G2 (коррекция лекарств): Task 6 + Task 14 (золотые кейсы; проверено на реальном справочнике).
- G3 (сохранность): Task 12 (`*_raw`), Task 13 (`raw_extraction`), Task 14 (сохранение сырого JSON).
- G4 (защита редких): Task 6 (`unverified`, без подмены).
- G5 (даты/единицы/числа): Task 3, 4, 5 + Task 14 (применение).
- G6 (подготовка изображений): Task 8.
- Справочник из ГРЛС-ZIP (8 списков, статусы/МНН/рег-номера): Task 7 (выполнен).

**Плейсхолдеры:** нет. `build_drug_reference.py` реализован полностью; `registry.jsonl` (20 948 названий) собран из официальной ZIP-выгрузки ГРЛС и закоммичен.

**Согласованность типов:** `prepare_images(path, long_side=...)`, `to_base64_jpegs(list)`, `default_options()`, `DrugNormalizer.correct/correct_free_text → DrugMatch(raw, canonical, type, mnn, statuses, reg_numbers, status, distance, ratio)`, `parse_date → (dt, raw)`, `parse_decimal → (value, raw)`, `canonical_unit → (canon, raw)`, `get_drug_normalizer()` — используются единообразно в задачах 8–14.
