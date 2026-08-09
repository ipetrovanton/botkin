# Статус-анализ проекта Botkin

> Дата начала: 2026-08-09 09:42
> Стек: Python 3.14.6, FastAPI, aiogram, Ollama, SQLite, Alpine.js

## Постановка

Пользователь попросил проанализировать текущее состояние проекта Botkin: что уже сделано, на каком этапе рефакторинг, что осталось, в каком статусе статья для Хабра и какие следующие шаги по реализации.

Критерий успеха — краткий, но конкретный ответ со ссылками на реальные метрики, ветки и файлы.

## Контекст и ограничения

- Окружение анализа: macOS 25.3.0, репозиторий `/Users/inna/botkin`.
- Ветка: `feat/pipeline-speed-accuracy`, HEAD `7facab4`.
- Локальная Ollama недоступна для части тестов; прогон unit-тестов без GPU возможен.
- Полная история исследований и замеров зафиксирована в `habr/lab-results-journal.md` и `TEST_RESULTS.md`.

## План

1. Изучить историю коммитов, ветку и diff.
2. Проверить тесты и линтер.
3. Посмотреть статус статьи и журнала для Хабра.
4. Сформулировать дальнейшие шаги.

## Ход работы

### Шаг 1: история и ветка

```
git log --oneline -40
```

Последние коммиты на ветке:
- `7facab4` fix(doctor_report): salvage study date from MRI header
- `6e045c0` test(e2e): assert doctor_report content, not only doc_type
- `97ef735` fix(extract): recover unit/ref swap and drop blank noise rows
- `56e6309` feat(pipeline): faster extract without e2e quality loss

От `master` отстаёт 4 коммита:
```
git log --oneline master..HEAD
7facab4 fix(doctor_report): salvage study date from MRI header
6e045c0 test(e2e): assert doctor_report content, not only doc_type
97ef735 fix(extract): recover unit/ref swap and drop blank noise rows
56e6309 feat(pipeline): faster extract without e2e quality loss
```

### Шаг 2: тесты и линтер

Unit-тесты (без `test_e2e_reasoning`, который дергает Ollama и зависает в этом окружении):
```
uv run pytest -m "not llm" -k "not test_e2e_reasoning" -q --tb=short
603 passed, 44 deselected, 6 warnings in 23.47s
```

Всего собрано 647 тестов, 35 помечены `@pytest.mark.llm`, 9 из `test_e2e_reasoning` не помечены, но требуют живую Ollama.

Линтер:
```
uv run ruff check src/ tests/
All checks passed!
```

### Шаг 3: рефакторинг

Последний каскад рефакторинга описан в `docs/habr_article.md` и коммитах с `2026-07-29`:
- разделение `repos.py` (1204 строки) на 6 доменных модулей;
- декомпозиция `extract.py` на salvage / text_extract / unit_correction / androflor;
- вынесение промптов в markdown-ресурсы `src/botkin/llm/prompts/`;
- разделение documents.py на тонкие роуты + сервис;
- введение типизированных Pydantic-настроек вместо `botkin.settings`;
- замена самописной валидации библиотечной.

Текущая ветка `feat/pipeline-speed-accuracy` продолжает линию скорость/точность:
- `long_side=1600`, early-exit voting, cleanup единиц;
- исправление swap unit/ref и noise-фильтр;
- salvage даты исследования из МРТ-заключений;
- e2e теперь проверяет содержимое заключений врача, не только `doc_type`.

### Шаг 4: статья для Хабра

Файлы:
- `habr/botkin-habr-article.md` — 373 строки, полная статья от введения до эпилога, без TODO/FIXME/WIP.
- `habr/lab-results-journal.md` — 3376 строк, подробный журнал итераций TDD.
- `docs/habr_article.md` — 277 строк, техническая заметка про бэкенды Ollama/MLX/vLLM.

Статья готова к вычитке, но содержит устаревшие цифры: в TL;DR упоминается 480 тестов и 25–27 с/док, тогда как сейчас 647 тестов и последний e2e на ветке ~13 с/док (34/34 PASS). Это нужно синхронизировать перед публикацией.

## Итог

- **Что сделано:** Telegram-бот, FastAPI-бэкенд, SPA-кабинет, пайплайн `classify → extract → normalize → persist`, нормализация по ГРЛС/ФСЛИ, RAG, интеграции Garmin/Apple Health/Strava, внешние факторы, админка, Docker Compose, 647 тестов.
- **Рефакторинг:** основная волна завершена; текущая ветка добивает точность и скорость OCR-пайплайна. E2E на 34 реальных документах — 34/34 PASS, ~13 с/док.
- **Что нужно сделать:** вмержить/протестировать `feat/pipeline-speed-accuracy`, обновить цифры в статье, рассмотреть оставшиеся unknown-рецепты, очередь VLM-вызовов и персистентность фоновых задач.
- **Статья:** черновик завершён, требует фактчекинга цифр.

## Материалы

- `git log --oneline` ветки `feat/pipeline-speed-accuracy` — актуальные коммиты.
- `habr/botkin-habr-article.md` — финальный черновик статьи.
- `habr/lab-results-journal.md` — журнал итераций.
- `TEST_RESULTS.md` — результаты прогонов.
- `HANDOFF.md` — инструкции для работы без GPU.
