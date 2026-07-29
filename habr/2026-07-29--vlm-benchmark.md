# Бенчмарк VLM/OCR-моделей для двухступенчатого распознавания меддокументов

> Дата начала: 2026-07-29 22:15
> Стек: Python 3.14, Ollama, qwen3-vl:8b-instruct, glm-ocr:latest, gemma4:latest

## Постановка

Пользователь продолжает работу по плану REVIEW_PLAN: фаза 7.1 — сравнить альтернативные VLM/OCR-модели на E2E-тестах, а фаза 7.2 — подготовить конфигурацию для двухступенчатого OCR → text-LLM пайплайна.

Критерий успеха: таблица в `benchmarks/models_comparison_YYYY-MM-DD.md` с precision, recall, медианным/средним временем, tps и VRAM для каждой модели; TEXT_MODEL должен оставаться по умолчанию.

## Контекст и ограничения

- OS: macOS, Python 3.14, Ollama запущен локально на `localhost:11434`.
- Проект: `botkin`, SQLite, FastAPI, Telegram-бот, веб-кабинет.
- Ollama-модели: `qwen3-vl:8b-instruct`, `glm-ocr:latest`, `gemma4:latest`.
- Важные env: `VLM_MODEL`, `TEXT_MODEL`, `OCR_MODEL`, `OLLAMA_URL`.
- Ограничение: запрещено переопределять `TEXT_MODEL` в бенчмарке, иначе сравнение перестаёт быть чистым.

## План

1. Поддержать `OCR_MODEL` в `config.py`/`client.py`/`image_ocr.py`/`sibr_ocr.py` (фаза 7.2).
2. Допилить `scripts/bench/bench_models.py`: precision/recall, tps, VRAM, markdown-отчёт и `--reparse`.
3. Запустить бенч на 3 моделях и дождаться результатов.
4. Синхронизировать `README.md` и `AGENTS.md` (фаза 8.1).

## Ход работы

### Шаг 1: добавлена поддержка `OCR_MODEL`

- `src/botkin/config.py`: `OCR_MODEL` и `OCR_*` параметры, по умолчанию берутся от `VLM_*`.
- `src/botkin/llm/client.py`: `ocr_options()` — отдельные опции для OCR-модели.
- `src/botkin/llm/image_ocr.py`, `sibr_ocr.py`: вызовы идут через `OCR_MODEL`/`OCR_MAX_TOKENS` + `ocr_options()`.
- Unit-тесты: `uv run pytest -q` — 569 passed.
- Коммит: `feat(ocr): dedicated OCR_MODEL config for two-stage pipeline`.

### Шаг 2: допиливание бенчмарк-скрипта

- Парсер `parse_pytest_summary` читает колонки precision/recall из итоговой сводки.
- `ModelResult`: precision/recall — средние по документам; score = precision × pass_rate / avg_time.
- `_parse_tps_per_doc` усредняет `tps=...` по всем вызовам внутри одного документа.
- `--reparse`: пересобирает JSON/MD-отчёт из `bench_*.log` без повторного запуска pytest.
- `print_comparison` и `save_markdown` показывают tps.
- Коммиты: `fix(bench): parse precision/recall from summary, add --reparse`; `feat(bench): capture tps from E2E logs`; `fix(bench): average tps across all calls per doc`.

### Сложность: парсер сводки ловил не те колонки

- **Симптом:** в консольном выводе `[BENCH] qwen3-vl:8b-instruct: ... | точность=0/0 (0.0%)` и score=0 для всех моделей.
- **Гипотезы:** старая регулярка захватывала только первые 6 колонок, при том что `e2e_report.py` печатает 9 колонок: name, status, classify, extract, total, precision, recall, mismatch.
- **Решение:** расширить `row_re` до 8 групп, парсить `precision`/`recall`, не пытаться парсить `matched/expected` из `mismatch`.
- **Урок:** формат вывода pytest-отчёта растёт; парсер должен падать, а не молча давать нули.

## Архитектурные решения

### Решение: `OCR_MODEL` через env-константы, а не через `_DEFAULTS`

- **Альтернативы:** A) добавлять `OcrConfig` в Pydantic-модели `Settings`; B) `OCR_MODEL = os.getenv("OCR_MODEL") or VLM_MODEL` в конце `config.py`.
- **Выбрано:** B — минимальные изменения, обратная совместимость без правки `config.json`, не требует перестройки `_build_settings`.
- **Компромисс:** `OCR_*` не видны в `Settings`, но публичные константы покрывают все параметры вызова.
- **Когда пересмотреть:** если появится отдельный `OCR` раздел в `config.json` с несколькими дефолтами.

### Решение: `--reparse` вместо перезапуска бенча

- **Альтернативы:** A) перезапускать бенч при каждом изменении парсера; B) хранить vram/wall в JSON и переспарсивать логи.
- **Выбрано:** B — экономит часы GPU-времени, позволяет допилить отчёт после прогона.
- **Компромисс:** log-файлы должны сохраняться; vram берётся из JSON, а не из `ollama ps`.
- **Когда пересмотреть:** когда формат лога станет стабильным и нет нужды дорабатывать парсер.

## Итог

- [ ] Бенч в процессе: `uv run python scripts/bench/bench_models.py --models qwen3-vl:8b-instruct glm-ocr:latest gemma4:latest`.
- [x] Инфраструктура OCR_MODEL готова.
- [x] Парсер и отчётность бенча поддерживают precision/recall/tps/vram.
- [x] README и AGENTS синхронизированы.

## Материалы

- [REVIEW_PLAN.md](/REVIEW_PLAN.md) — утверждённый план.
