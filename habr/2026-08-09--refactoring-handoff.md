# Рефакторинг: очередь, lifestyle, config.json, модули UI

> Дата начала: 2026-08-09 10:45
> Стек: Python 3.12, FastAPI, pytest, ruff, Alpine.js

## Постановка

Пользователь принял предложения по рефакторингу, найденные в предыдущей сессии, и попросил:

1. Пометить `tests/test_e2e_reasoning.py` маркером `@pytest.mark.llm`, чтобы без GPU он не висел.
2. Вынести дефолты из `src/botkin/config.py` в JSON-файл, оставив в `config.py` только pydantic-модели.
3. Разбить монолит `src/botkin/web/app.js` (`cabinet()`, 1385 строк) на модули: документы, здоровье, ассистент, админ.
4. Вынести сборщики RAG-контекста из `src/botkin/rag/recommend.py` в `src/botkin/rag/context.py`.
5. Обновить/удалить устаревший `HANDOFF.md` (там 313 passed, реальность — 613).
6. Закрепить находки живого прогона lifestyle: `sqlite3.Row` и отказ embed-модели.

Критерий успеха — все unit-тесты и lint проходят, изменения закоммичены, фактура пополнена.

## Контекст и ограничения

- Без GPU e2e VLM-прогоны недоступны; основная проверка — `pytest -m "not llm"`.
- `app.js` использует Alpine.js без бандлера; разделение должно сохранить `x-data="cabinet()"` и не ломать event-обработчики.
- `config.py` — горячий путь; любое изменение приоритета env → config.json → defaults должно остаться прозрачным.

## План

1. `test_e2e_reasoning.py`: заменить `pytestmark = pytest.mark.reasoning` на список `[pytest.mark.llm, pytest.mark.reasoning]`.
2. `config.py`:
   - создать `src/botkin/defaults.json` из текущего `_DEFAULTS`;
   - заменить `_DEFAULTS` на загрузку `_defaults` из `defaults.json`;
   - `_get` смотрит сначала `config.json`, потом `defaults.json`.
3. `app.js`: выделить `healthModule`, `assistantModule`, `adminModule`, `documentsModule`; сохранить `cabinet()` как композицию.
4. `rag/context.py`: перенести `_patient_context`, `_profile_context`, `_reports_context`, `_external_context` и SQL.
5. `HANDOFF.md`: актуализировать цифры и открытый техдолг.
6. Дописать регрессионный тест на `sqlite3.Row` и graceful-отказ embed-модели.
7. Прогнать `pytest -m "not llm"` и `ruff`.
8. Закоммитить.

## Ход работы

### Шаг 1: test_e2e_reasoning.py — маркер `@pytest.mark.llm`

`pytestmark` был только `reasoning`, поэтому без GPU тест висел, пока не упал по таймауту.
Заменил на список:

```python
pytestmark = [pytest.mark.llm, pytest.mark.reasoning]
```

### Шаг 2: вынес defaults из `config.py` в JSON

Сгенерировал `src/botkin/defaults.json` из текущего `_DEFAULTS`, убрал ~150 строк
Python-словаря. Приоритет остался: env → `config.json` → `defaults.json`.

Сложность: первый вариант `Path(__file__).with_suffix(".defaults.json")` давал
`config.defaults.json`, а файл назывался `defaults.json` — defaults загружались пустым
сетом, и `_get("auth.admin_telegram_ids")` вернул `None`, на чем `frozenset(...)` упал:

```
TypeError: 'NoneType' object is not iterable
```

Поправил на `Path(__file__).with_name("defaults.json")`. Юнит-тесты прошли.

### Шаг 3: вынес RAG-контекст в `rag/context.py`

Перенес `_patient_context`, `_profile_context`, `_reports_context`, `_external_context`
и SQL в новый модуль. `recommend.py` теперь импортирует `build_patient_context`.
Это убрало смешение сборки контекста и генерации рекомендации.

### Шаг 4: разбил `app.js` на модули

Субагент переработал 1385 строк в четыре фабрики:
- `documentsModule(app)` — документы, фильтры, верификация, загрузка/очередь, аналитика;
- `healthModule(app)` — Garmin/Strava/Apple Health, графики;
- `assistantModule(app)` — RAG-вопросы, lifestyle, индексация, бенчмарк;
- `adminModule(app)` — пользователи, лабораторные правки.

`cabinet()` остался композицией: общее состояние (`screen`, auth, theme, API, formatters)
осталось внутри, а доменные методы привязаны к экземпляру через `bindMethods(app, methods)`.
Для сохранения существующих HTML-обработчиков созданы топ-уровневые алиасы
(`askAssistant`, `connectGarmin`, `adminCreateUser` и т.д.).
`node --check src/botkin/web/app.js` проходит.

### Шаг 5: обновил `HANDOFF.md` и тесты

- Подправил цифру: 313 → 613 passed.
- Указал, что `test_e2e_llm.py` и `test_e2e_reasoning.py` помечены `@pytest.mark.llm`.
- Перевёл вызовы `_patient_context` в тестах на `build_patient_context`.

### Итог

- `pytest -m "not llm"`: **613 passed, 44 deselected**.
- `ruff check src tests`: clean.
- `node --check app.js`: clean.
- Все правки закоммичены в `feat/queue-and-lifestyle-recs`.

