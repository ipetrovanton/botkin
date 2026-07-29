# План устранения недостатков проекта botkin

> Документ — пошаговый промт для исполнителя (легковесной LLM или разработчика).
> Каждый шаг: **Цель → Файлы → Действия → Критерий приёмки → Проверка**.
> Выполнять фазы строго по порядку. Внутри фазы шаги независимы, если не указано иное.
> После каждого шага: `uv run ruff check src tests scripts && uv run pytest -q` — должно быть зелёным.
> Коммит после каждого шага, Conventional Commits, subject ≤ 50 символов.

Дата ревью: 2026-07-29. Базис: Python 3.12.9, ~23 700 строк Python (src+tests+scripts).

---

## Фаза 0. Базовая линия (перед любыми правками)

### Шаг 0.1 — Зафиксировать зелёную базу

- **Действия:** запустить `uv run pytest -q`, `uv run ruff check src tests scripts`, сохранить вывод в заметку. Если что-то красное — сначала починить/зафиксировать как known-issue.

- **Приёмка:** есть записанная точка отсчёта (число тестов, время прогона).

---

## Фаза 1. Чистка репозитория от артефактов

### Шаг 1.1 — Удалить мусорные файлы из корня

- **Файлы:** `test_image.jpg`, `test_image.png`, `test_baseline.jpg`, `extract_text.py` (одноразовый скрипт с хардкод-путём Windows), `botkin_research_report.docx`, `.env.gitignore` (пустой, правило `.env` уже есть в `.gitignore`).

- **ВАЖНО (найдено при выполнении):** `TEST_RESULTS.md` — НЕ одноразовый отчёт, это живой файл, на который
  явно ссылается `HANDOFF.md` («Итог последнего полного e2e-прогона см. в `TEST_RESULTS.md`»). Не удалять.

- **Действия:** проверить `grep -rln "<имя файла>" src tests scripts docs *.md` — что никто не ссылается;
  удалить; `botkin_research_report.docx` перенести в `habr/` (на него ссылается `docs/datasets-for-validation.md`
  — обновить путь в ссылке).

- **Приёмка:** в корне остаются только конфиги, манифесты, README/AGENTS/HANDOFF/TEST_RESULTS/LICENSE, docker-файлы.

### Шаг 1.2 — Вычистить scripts/bench

- **Файлы:** `scripts/bench/*.txt`, `*.log`, `*.bak`, одноразовые отладочные скрипты
  (`bench_api_compare.py`, `bench_check.py`, `bench_debug_ocr.py`, `bench_debug_parse.py`,
  `bench_diagnostic.py`, `bench_fail_details.py`, `bench_fails.py`, `bench_instructor.py`,
  `bench_modes.py`, `bench_openai.py`, `bench_print.py`, `bench_progress.py`, `bench_raw.py`,
  `bench_regressions.py`, `bench_test_parse.py`).

- **ВАЖНО (найдено при выполнении):** `bench_models_results.json`, `bench_expectations_results.json`,
  `bench_reasoning_results.json`, `bench_expectations_report.md` — это НЕ одноразовые дампы, а живое
  состояние, которое `bench_models.py`/`bench_expectations.py`/`bench_reasoning.py`/`bench_compare.py`
  читают и дописывают на каждом прогоне (константы `RESULTS_FILE` внутри). Переносить/переименовывать
  их с датой НЕЛЬЗЯ — сломает сравнение с baseline. Оставить как есть на месте.

- **Действия:**

  1. Удалить `.bak`, `.log`, `.txt`-дампы из git (`git rm`)/с диска, добавить в `.gitignore`:
     `scripts/bench/*.txt`, `scripts/bench/*.log`, `*.bak`.

  2. Удалить перечисленные одноразовые отладочные скрипты (`git rm`) — проверить `grep -rn "<имя>"
     HANDOFF.md TEST_RESULTS.md docs` перед удалением: `bench_compare.py` оказался задокументированным
     рабочим шагом в `HANDOFF.md`/`TEST_RESULTS.md` — НЕ удалять.

  3. Оставить: `bench_runner.py`, `bench_models.py`, `bench_reasoning.py`, `bench_expectations.py`,
     `bench_health_report.py`, `bench_compare.py`, `bench_uncensored_rag.py`, `analyze_uncensored_rag.py`,
     `_smoke_rag.py` — все они либо документированы, либо не одноразовые.

- **Приёмка:** `ls scripts/bench` — только `.py`-файлы + живые `*_results.json`/`*_report.md`, ни одного `.txt`/`.log`/`.bak`.

### Шаг 1.3 — Вычистить tests/ от дампов

- **Файлы:** `tests/_dump_clean.txt`, `_dump_output.txt`, `_dump_utf8.txt`, `_last_run.txt`, `tests/_dump_extractions.py`.

- **ВАЖНО (найдено при выполнении):** все 5 файлов оказались untracked (`git ls-files` их не находит) —
  просто локальный мусор разработчика, не в истории git. `_dump_extractions.py` — сам себя описывает
  как «Одноразовый дамп извлечённых показателей» в докстринге, утилитой не является.

- **Действия:** удалить с диска (`rm`, без `git rm` — не отслеживались); добавить `tests/_dump*`,
  `tests/_last_run*` в `.gitignore`, чтобы не попали повторно.

- **Приёмка:** в `tests/` только `test_*.py`, `conftest.py`, `fixtures/`.

---

## Фаза 2. Зависимости и Python

Актуальные версии проверены 2026-07-29 (PyPI/GitHub releases):
Python 3.14.6 (стабильная, 2026-06-10), fastapi 0.140.8, pydantic 2.13.4,
aiogram 3.30.0 (Bot API 10.2), instructor 1.15.4.

### Шаг 2.1 — Удалить неиспользуемые зависимости

- **Файл:** `pyproject.toml`.

- **Действия:** grep-ом подтвердить и удалить из dependencies то, что нигде не импортируется:

  - `pyyaml` — 0 импортов;

  - `markdown` — 0 импортов;

  - `pydantic-settings` — 0 импортов (НО: см. шаг 3.1 — вместо удаления начать реально использовать).

  - Проверить `openai`: используется только как транспорт instructor? Если прямых импортов нет вне `llm/client.py` — оставить (нужен instructor), зафиксировать минимальную версию.

- **Проверка:** `uv sync && uv run pytest -q`.

### Шаг 2.2 — Обновить зависимости до актуальных

- **Файл:** `pyproject.toml`, затем `uv lock --upgrade`.

- **Действия (по одной группе за раз, с прогоном тестов):**

  1. `fastapi==0.136.1` → `>=0.140.8`; `uvicorn==0.34.0` → последняя; `python-multipart` → последняя.

  2. `pydantic==2.10.4` → `>=2.13.4` (2.13 добавила поддержку Python 3.14). Прогнать все тесты моделей.

  3. `aiogram==3.28.2` → `>=3.30.0` (Bot API 10.2). Проверить bot-хендлеры.

  4. `instructor==1.7.2` → `>=1.15.4` — **мажорный скачок**, читать changelog 1.8–1.15: менялись `from_openai`/`Mode`. Прогнать `tests/test_llm_calls.py`, `tests/test_client_*.py`.

  5. `plotly==5.24.1` → `>=6`; `kaleido==0.2.1` → `>=1.0` (v1 — полностью переписан, API `write_image` сохранён). Проверить `tests/test_make_lab_pdf.py`, `viz/plots.py`.

  6. dev: `pytest==8.3.4`, `pytest-asyncio==0.25.2`, `ruff==0.8.6` → последние.

- **Правило:** жёсткие пины `==` заменить на `>=X,<X+1` (совместимые диапазоны), точные версии держит `uv.lock`.

- **Приёмка:** `uv lock --upgrade` без конфликтов, тесты зелёные после каждой группы.

### Шаг 2.3 — Поднять Python

- **Файлы:** `.python-version`, `pyproject.toml` (`requires-python`), `Dockerfile`.

- **Действия:**

  1. Сначала консервативно: `3.12.9` → `3.13.14` (последний bugfix 3.13), `requires-python = ">=3.13,<3.15"`.

  2. Затем попытка 3.14.6: `uv python install 3.14 && uv run --python 3.14 pytest -q`. Блокеры вероятны в бинарных колёсах (`opencv-python-headless`, `sqlite-vec`, `kaleido`) — если колёс под 3.14 нет, остаться на 3.13 и записать TODO с датой пересмотра.

- **ВАЖНО (найдено при выполнении):** первая попытка `uv run --python 3.14 pytest` на этой машине упала
  с `AttributeError: 'sqlite3.Connection' object has no attribute 'enable_load_extension'` в
  `src/botkin/rag/store.py` (5 тестов `test_rag_store.py`/`test_health_api.py`). Причина — не сам
  Python 3.14, а конкретная СИСТЕМНАЯ сборка (Homebrew/python.org на macOS), которая линкуется
  с ограниченным Apple `libsqlite3` без поддержки loadable extensions. uv-managed сборка
  (`python-build-standalone`, `uv python install 3.14`) собрана с полноценным sqlite3 и работает
  корректно. **Вывод:** при диагностике подобных блокеров на бинарных колёсах/расширениях —
  сначала проверить `python -c "import sqlite3; print(hasattr(sqlite3.connect(':memory:'),
  'enable_load_extension'))"` на РАЗНЫХ сборках интерпретатора той же версии, прежде чем делать
  вывод «X.Y не готова». `.python-version` фиксировать как generic `3.14` (без платформенного
  суффикса `-macos-aarch64-none`), чтобы на Windows-хосте uv сам подобрал подходящую сборку.

- **Приёмка:** тесты зелёные на выбранной версии; версия одинакова в `.python-version`, `pyproject.toml`, `Dockerfile`.

### Шаг 2.4 — Замены на более сильные библиотеки

- **Действия:**

  1. Валидация email: убрать самописный `_EMAIL_RE` в `src/botkin/api/routes/auth.py` → pydantic `EmailStr` (+ зависимость `email-validator`).

  2. Хеширование паролей: проверить `src/botkin/db/user_repo.py` — если самописное (sha/pbkdf2 вручную) → заменить на `argon2-cffi` или `bcrypt` с миграцией хешей при логине.

  3. `scripts/bench/*` замеры времени → использовать общий `botkin/llm/timing.py`, а не ручные `time.perf_counter()`.

  4. HTTP-клиенты: в боте создаётся `httpx.AsyncClient` на каждый аплоад (`bot/handlers/upload.py::_upload_to_api`) — вынести один клиент на процесс (lifespan-паттерн).

  5. НЕ трогать: `rapidfuzz`, `dateparser`, `tenacity`, `json-repair`, `sqlite-vec` — уже правильный выбор.

- **Приёмка:** ruff + тесты зелёные; для каждой замены — блок «Почему так» в описании коммита.

---

## Фаза 3. Единая конфигурация (устранить двойную систему)

**Проблема:** сосуществуют `src/botkin/config.py` (678 строк, самописный merge env/json/defaults)
и недоделанный пакет `src/botkin/settings/` (7 файлов), который **никто не импортирует**
кроме самого себя. При этом `pydantic-settings` объявлен в зависимостях и не используется.
Плюс баг: в `settings/loader.py` `_project_root = Path(__file__).parent.parent.parent`
указывает на `src/`, а не на корень — `config.json` оттуда не читается.

### Шаг 3.1 — Достроить settings/ на pydantic-settings

- **Файлы:** `src/botkin/settings/*.py`.

- **Действия:**

  1. Переписать модели настроек на `pydantic_settings.BaseSettings` с `SettingsConfigDict(env_file=".env")` и кастомным JSON-источником для `config.json` (`settings_customise_sources` + `JsonConfigSettingsSource`). Приоритет: env > config.json > дефолты — как сейчас.

  2. Исправить путь к корню проекта (4 `.parent` от `settings/loader.py` или поиск `pyproject.toml` вверх по дереву).

  3. Секции: `vlm`, `text_model`, `ollama`, `pdf_to_image`, `image`, `database`, `bot`, `storage`, `upload`, `drugs`, `analytes`, `rag` — один-в-один с `_DEFAULTS` из `config.py`.

- **Приёмка:** `get_settings()` возвращает те же значения, что константы `config.py` (написать паритет-тест `tests/test_settings_parity.py` ДО миграции).

### Шаг 3.2 — Мигрировать импортёров и удалить config.py

- **Действия:**

  1. `grep -rn "from botkin.config import" src tests scripts` — список потребителей.

  2. Мигрировать модулями (llm → preprocess → db → api → bot → rag), после каждого — тесты.

  3. На переходный период `config.py` реэкспортирует значения из `settings` (фасад), затем удалить.

- **Приёмка:** `config.py` удалён либо ≤ 30 строк фасада; паритет-тест удалён вместе с фасадом; тесты зелёные.

---

## Фаза 4. Промты: отделить от кода и оптимизировать

**Проблема:** основной набор в `llm/prompts.py`, но промты также захардкожены в
`llm/image_ocr.py` (5 шт.), `llm/extract.py` (4), `rag/recommend.py` (2), `llm/sibr_ocr.py` (1).
Версионирование — одна строка `PROMPTS_VERSION` на всё.

### Шаг 4.1 — Вынести промты в ресурсы

- **Действия:**

  1. Создать `src/botkin/llm/prompts/` — каталог Markdown-файлов с YAML frontmatter:
     `classify.md`, `analysis_vlm.md`, `analysis_text.md`, `analysis_text_compact.md`,
     `doctor_report.md`, `image_ocr.md`, `sibr_ocr.md`, `androflor_ocr.md`, `rag_recommend.md`.
     Frontmatter: `version: 2026-07-29`, `model_target: qwen3-vl:8b-instruct`, `purpose: ...`.

  2. Лоадер `prompts/__init__.py`: читает файлы через `importlib.resources`, кэширует,
     отдаёт `Prompt(name, version, system, instruction)`. Логировать version per-prompt
     (заменяет глобальный `PROMPTS_VERSION`).

  3. Перенести все inline-промты из `image_ocr.py`, `extract.py`, `recommend.py`, `sibr_ocr.py`.

  4. `tests/test_prompts.py` дополнить: каждый файл читается, frontmatter валиден, нет пустых.

- **Приёмка:** `grep -rn '"""Ты' src/botkin --include='*.py'` — 0 совпадений вне тестов.

### Шаг 4.2 — Оптимизировать тексты промтов

- **Действия (по одному промту, с прогоном bench_expectations до/после):**

  1. Убрать дублирование: `ANALYSIS_TEXT_COMPACT_SYSTEM` собирается конкатенацией срезов строки (`.split(...)`) — заменить на явные общие блоки-фрагменты в лоадере.

  2. Сократить повторы «ДОСЛОВНО/не выдумывай» до одного чёткого блока-ограничения на промт (VLM инструкции-повторы after 3-го раза не добавляют точности, но жгут контекст).

  3. Все few-shot примеры — в конец промта; форматные требования — маркированным списком.

  4. Каждое изменение — новый `version` в frontmatter + запись результата бенча в `benchmarks/`.

- **Приёмка:** метрики `scripts/bench/bench_expectations.py` не хуже базовой линии; суммарный размер system-промтов снижен ≥ 15%.

---

## Фаза 5. Академическая структура кода

### Шаг 5.1 — Разбить крупные модули

- **Файлы и целевые размеры:**

  1. `src/botkin/llm/extract.py` (654) → `extract.py` (оркестрация), `salvage.py` (починка обрезанного JSON), `mapping.py` (маппинг ответа в domain-модели). Цель ≤ 250 строк каждый.

  2. `src/botkin/config.py` (678) — исчезает в Фазе 3.

  3. `src/botkin/db/document_repo.py` (405) → выделить `document_queries.py` (SQL-константы) либо разнести read/write методы. Цель ≤ 300.

  4. `src/botkin/api/routes/documents.py` (387) → тонкие роуты + `api/services/documents.py` (бизнес-логика верификации/правки).

  5. `scripts/bench/bench_health_report.py` (780) → раннер + модуль сценария.

- **Правило:** чистое перемещение кода, поведение не меняется, публичные импорты сохраняются реэкспортом.

- **Приёмка:** ни одного модуля > 400 строк в src/ (кроме сгенерированных данных); тесты зелёные без правок самих тестов.

### Шаг 5.2 — Разделение слоёв в боте и API

- **Действия:**

  1. Хендлеры бота (`bot/handlers/*.py`) открывают соединение с БД напрямую (`get_conn` в каждом). Вынести повторяющийся паттерн «resolve user → repo call» в `bot/services.py` (3–5 функций), хендлеры оставить тонкими (parse → service → render).

  2. `api/routes/auth.py`: валидация email → pydantic-модель (шаг 2.4), магические числа cookie уже в константах — ок.

  3. Проверить остальные роуты: SQL и построение ответов не должны жить в роуте — только вызовы repo/service.

- **Приёмка:** в `bot/handlers/` нет прямых SQL; каждый хендлер ≤ 40 строк.

### Шаг 5.3 — Единообразие docstring и типов

- **Действия:** публичные функции src/ — с аннотациями типов и docstring «зачем»; `Generator[dict, None, None]` → современный `Iterator[dict]` где применимо; `typing.Generator` → `collections.abc`.

- **Проверка:** `uv run ruff check --select ANN,D --statistics src` (включить правила постепенно, не чинить всё сразу — только публичные API).

---

## Фаза 6. E2E-тесты с детальными метриками

**Сейчас:** `tests/test_e2e_llm.py` печатает `[SPEED]` и грубые бюджеты (180/900 с).
**Нужно:** скорость, токены/с, размеры контекста, структурный diff expected vs actual.

### Шаг 6.1 — Собрать метрики инференса из Ollama

- **Файлы:** `src/botkin/llm/client.py`, `src/botkin/llm/timing.py`.

- **Действия:**

  1. Ollama в ответе `/api/chat` (и через OpenAI-совместимый эндпоинт в поле `usage`) отдаёт: `prompt_eval_count` (токены контекста), `eval_count` (токены ответа), `eval_duration`, `total_duration`. Собрать в датакласс `InferenceMetrics(model, prompt_tokens, completion_tokens, tokens_per_second, elapsed_s, num_ctx)`.

  2. `timing.timed()` расширить полем `metrics`; клиент заполняет его после каждого вызова.

  3. Логировать: `[METRICS] model=... ctx=1234/8192 out=567 tps=41.2 t=13.8s`.

- **Приёмка:** юнит-тест с замоканным ответом Ollama проверяет расчёт tps и заполнение полей.

### Шаг 6.2 — Структурный diff в e2e

- **Файлы:** `tests/test_e2e_llm.py`, новый `tests/e2e_report.py`.

- **Действия:**

  1. Сверку с sidecar `*.expected.json` переписать на пофакторный diff: для каждого показателя выводить `MISSING` (есть в expected, нет в actual), `EXTRA`, `MISMATCH parameter=Гемоглобин field=value expected='13.7' actual='137'`.

  2. Итоговая сводка per-документ: precision/recall по строкам показателей, число mismatch по полям (value/unit/reference_range).

  3. При падении assert печатать полный diff-отчёт, а не только счётчики.

- **Приёмка:** прогон `uv run pytest -m llm -s` на fixtures/documents печатает per-doc таблицу: время classify, время extract, prompt_tokens, completion_tokens, tps, P/R, список mismatch.

### Шаг 6.3 — Отчёт e2e в benchmarks/

- **Действия:** после прогона `-m llm` писать `benchmarks/e2e_YYYY-MM-DD_HHMM_<model>.json`: окружение (модель, версия Ollama, commit), метрики per-doc, агрегаты (медиана tps, p95 времени, суммарные P/R). Формат совместим со `scripts/bench/bench_models.py` — для сравнения между запусками.

- **Приёмка:** два последовательных прогона дают два файла, скрипт `scripts/bench/bench_compare.py` умеет их сравнить.

### Шаг 6.4 — Ревизия юнит-тестов

- **Действия:**

  1. `pytest -q --durations=10` — найти медленные не-llm тесты, ускорить (мок вместо реального sleep/IO).

  2. Проверить тесты на тривиальность (тесты геттеров, тестирование деталей реализации `_format_*` через реэкспорт в `show.py`) — переориентировать на публичный контракт `compose_card`.

  3. Названия — полные сценарии: `test_upload_rejects_corrupted_pdf_with_422`.

- **Приёмка:** прогон юнит-тестов ≤ 60 с; нет тестов приватных функций через реэкспорты.

---

## Фаза 7. Оптимизация моделей: точность и производительность

### Шаг 7.1 — Бенчмарк альтернативных VLM

Кандидаты по свежим публичным бенчам локального OCR (nullmirror, 2026-05; mylocalai, 2026):

- `qwen3-vl:30b-a3b` — максимум точности (MoE, активных 3B — быстрее плотной 30b), уже частично мерился (`bench_qwen3-vl_30b-a3b.log`);

- `glm-ocr` / `deepseek-ocr` — OCR-специализированные: очень быстрые на печатном тексте (~10 c/страница), кандидаты на первую ступень двухступенчатого пайплайна;

- `minicpm-v:8b` — по бенчам ненадёжен (галлюцинирует числа) — из кандидатов исключить.

- **Действия:** прогнать `scripts/bench/bench_models.py` на fixtures/documents для каждого кандидата; сравнить P/R показателей, tps, VRAM (`ollama ps`); результаты — в `benchmarks/`.

- **Приёмка:** таблица в `benchmarks/models_comparison_YYYY-MM-DD.md`: модель × (precision, recall, медиана времени, tps, VRAM).

### Шаг 7.2 — Двухступенчатый пайплайн OCR → текстовая LLM

- **Идея:** уже реализован паттерн «текстовый слой PDF → лёгкая текстовая модель» с компактным построчным форматом (в 1.9–2.4 раза быстрее JSON). Расширить его на сканы:

  1. Ступень 1: OCR-специализированная модель (`glm-ocr`/`deepseek-ocr`) переводит скан в текст-разметку.

  2. Ступень 2: лёгкая текстовая модель (`qwen3:1.7b`/`qwen3:8b`) раскладывает строки по колонкам через существующий `ANALYSIS_TEXT_COMPACT_SYSTEM` + `parse_compact_rows()`.

  3. VLM-путь оставить фолбэком при пустом/битом OCR-выводе (как сейчас compact → JSON fallback).

- **Приёмка:** на bench-наборе точность не хуже прямого VLM-пути, медианное время на страницу ниже ≥ 30%.

### Шаг 7.3 — Параметры инференса

- **Действия (мерить каждый пункт отдельно через bench):**

  1. `num_ctx`: посчитать реальный `prompt_eval_count` по метрикам шага 6.1 и подобрать минимальный достаточный (сейчас 8192 в config.json / 16384 в дефолтах — рассинхрон, устранить).

  2. Квантование KV-кэша Ollama: `OLLAMA_KV_CACHE_TYPE=q8_0` — экономия VRAM до ~50% на длинных контекстах, потери точности минимальны; проверить на bench.

  3. `OLLAMA_NUM_PARALLEL` для мультистраничных PDF: страницы одного документа обрабатывать параллельно, если VRAM позволяет.

  4. `keep_alive` уже 30m — ок; убедиться, что classify и extract используют одну и ту же модель, чтобы не было выгрузки/загрузки между стадиями.

- **Приёмка:** зафиксированный в `benchmarks/` протокол: параметр → метрика до/после.

### Шаг 7.4 — RAG-усиление точности извлечения

- **Действия:**

  1. Пост-валидация показателей через справочник ФСЛИ (`reference/analytes/registry.jsonl`): извлечённый параметр не сматчился с каноном → пометить `low_confidence`, показать пользователю на верификацию (флоу верификации в API уже есть).

  2. Валидация единиц: канонический аналит имеет ожидаемые единицы (`reference/units.py`) — расхождение «г/л vs г/дл» ловить детерминированно, а не промтом.

  3. Retrieval-примеры в промт: для повторных загрузок бланков той же лаборатории подмешивать 2–3 уже верифицированных пользователем строки из прошлых документов той же клиники (few-shot из своей истории) — самый дешёвый прирост точности на повторяющихся форматах.

  4. Бенчмарк эмбеддингов (`rag/benchmark.py`, hit_rate/MRR) прогнать для `bge-m3` vs новых кандидатов из Ollama (проверить наличие свежих embedding-моделей на дату выполнения).

- **Приёмка:** на bench-наборе recall canonical-маппинга аналитов ≥ базового, число ложных единиц падает; результаты в `benchmarks/`.

### Шаг 7.5 — Точность через self-consistency (опционально, если VRAM позволяет)

- **Идея:** для документов с `classify.confidence < 0.7` или пустым извлечением — второй прогон extract с temperature 0.2 и сравнение: совпавшие строки принять, разошедшиеся — пометить на верификацию.

- **Приёмка:** мерить на bench: прирост recall против прироста времени; включать фичефлагом в настройках.

---

## Фаза 8. Финализация

### Шаг 8.1 — Синхронизация документации

- **Действия:** обновить `AGENTS.md` (структура, конфигурация, промты-ресурсы), `README.md` (версии Python/зависимостей, запуск бенчей), удалить устаревшие разделы `HANDOFF.md`.

### Шаг 8.2 — Контрольный прогон

- **Действия:** `uv run ruff check`, `uv run pytest -q`, `uv run pytest -m llm -s` (при доступной Ollama), полный bench, сравнение с базовой линией Фазы 0.

- **Приёмка:** всё зелёное; метрики не хуже базовой линии; отчёт о дельте в `benchmarks/`.

---

## Сводка ключевых находок ревью (обоснование плана)

1. **Двойная система конфигурации:** `config.py` (678 строк) + мёртвый пакет `settings/` с багом пути к корню; `pydantic-settings` в зависимостях, но не используется.

2. **Неиспользуемые зависимости:** `pyyaml`, `markdown`, `pydantic-settings` (0 импортов).

3. **Устаревшие пины:** pydantic 2.10.4 (актуальная 2.13.4), instructor 1.7.2 (1.15.4), fastapi 0.136.1 (0.140.8), aiogram 3.28.2 (3.30.0), plotly 5.x (6.x), kaleido 0.2.1 (1.x).

4. **Python:** 3.12.9 при актуальной 3.14.6; `requires-python <3.14` блокирует апгрейд.

5. **Промты частично в коде:** inline-промты в `image_ocr.py`, `extract.py`, `recommend.py`, `sibr_ocr.py`; версия одна на всё.

6. **Мусор в git:** дампы `.txt`/`.log`/`.bak` в `scripts/bench/` и `tests/`, тестовые картинки и `.docx` в корне.

7. **e2e без метрик:** нет tokens/sec, размеров контекста и структурного diff expected/actual.

8. **Рассинхрон конфигов:** `config.json` (num_ctx 8192, extract_long_side 2200) vs `_DEFAULTS` (16384, 1280).

9. **Крупные модули:** `extract.py` 654, `config.py` 678, `bench_health_report.py` 780 строк.
