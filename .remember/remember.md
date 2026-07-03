# Текущий handoff

## Итерация 27: веб-кабинет пациента (НЕ закоммичено) — АКТУАЛЬНО

Вынес функционал Telegram-бота в полноценный мобильный веб-кабинет (SPA). Ветка
`fix/ocr-stability-accuracy`, поверх коммита `eb319be`. Рабочее дерево с новыми файлами,
НЕ закоммичено, НЕ запушено.

### Что сделано
- **Фронтенд** `src/botkin/web/`:
  - `index.html` — каркас SPA на Alpine.js: 7 экранов (Обзор, Документы, Детальная карточка,
    Загрузка, Аналитика/динамика, Заключения, Профиль) + mobile bottom-nav (5 пунктов с
    центральным FAB-аплоадом) + шапка с лого BOTkin и ECG-анимацией (SVG stroke-dashoffset).
  - `styles.css` — дизайн-система: бренд teal/cyan градиент, тёмная тема дефолт + свет,
    CSS-переменные, все иконки inline SVG, анимации CSS keyframes (пульс, float, shimmer,
    fade-in), `prefers-reduced-motion` уважается, `env(safe-area-inset-*)` под notch.
  - `app.js` — компонент `cabinet()`: API-клиент (fetch + X-Telegram-User-Id), все экраны,
    SVG-график динамики в DOM (цвета из CSS-переменных, перерисовка при смене темы),
    поллинг статуса загрузки с прогрессом по стадиям, идентификация demo (localStorage).
  - `vendor/alpine.min.js` — Alpine.js 3.15.12 (MIT, релиз 2026-04-30), заендорено локально
    (46 КБ), без CDN — офлайн-работа.
- **Бэкенд** `src/botkin/api/`:
  - `routes/documents.py` — /api/me, /api/documents (фильтры+пагинация), /api/documents/{id},
    /api/documents/{id}/status.
  - `routes/analytics.py` — /api/analytes, /api/clinics, /api/doctors, /api/dynamics,
    /api/labs/period, /api/reports, /api/stats.
  - `app.py` — роуты зарегистрированы ДО mount статики; `StaticFiles(html=True)` в `/`.
- **Репозитории** `src/botkin/db/repos.py` — DocumentRepo.search (EXISTS по врачу),
  distinct_clinics, date_range, stats; LabRepo.distinct_analytes;
  ReportRepo.distinct_doctors, for_period (JOIN documents → clinic).
- **Баг-фикс** `src/botkin/db/connection.py` — `create_function("lower",1,Python-lower)` в
  get_conn(): SQLite без ICU не опускает регистр кириллицы, поиск `q=биохим` возвращал 0.
  Попутно починило существующий dynamics (LIKE по analyte_name).
- **Тесты** `tests/test_cabinet_repo.py` (12) + `tests/test_cabinet_api.py` (13) = 25 новых.
- **Протокол** — habr/lab-results-journal.md Итерация 27 (строки 2328–2430).

### Результат
- `ruff check src tests` → clean.
- `pytest -m "not llm"` → **341 passed** (316 + 25 новых), 35 deselected, 1 warning (httpx
  deprecation в TestClient — не критично).
- node `--check app.js` → синтаксис OK; структура index.html валидна.
- Live-сервер (127.0.0.1:8000): все 11 эндпоинтов 200, статика отдаётся, поиск по кириллице
  работает (биохим→1, инвитро→15, крови→6 на реальной БД user 113521070).
- Запуск: `.venv\Scripts\python.exe -m uvicorn botkin.api.app:app --host 0.0.0.0 --port 8000`,
  открыть http://localhost:8000. Demo-пользователь 113521070 (есть данные: 32 документа,
  276 показателей, 9 заключений). Профиль → «Использовать demo».

### Следующий шаг
1. Ревью/коммит ветки (по команде оператора) — новые файлы + правки.
2. При желании: визуальная проверка в реальном браузере (мобильный вид, анимации).
3. Открытый техдолг: морфологический поиск (сейчас подстрочный — «кровь» ≠ «крови»);
   полноценная авторизация вместо demo-идентификатора; SSR/SEO если понадобятся публичные
   страницы.

---

## (предыдущее) Ветка: fix/ocr-stability-accuracy — оптимизация скорости+точности (закоммичено)

4 коммита поверх `9537180`. Рабочее дерево чистое. НЕ запушено.

### Что сделано в этой сессии
- **config**: num_predict 16384→4096, num_ctx 16384→8192, temperature 0.0 (extract) /
  0.1 (classify, ключ `vlm.classify_temperature`), repeat_penalty 1.1.
- **fix(llm)**:
  - adaptive fallback против пустого ответа XGrammar на текстовом пути `_structure_text`
    (бюджет `_TEXT_EMPTY_RETRIES=2`) — устранил плавающие FAIL (sample_001 3/6 → 6/6);
  - `classify._correct_classification_by_content`: корректор типа по заголовку
    (`_ANALYSIS_TITLE_KEYWORDS`) — «Общий анализ крови» → analysis (чинит sample_011);
  - `_extract_once` декомпозирован; удалён мёртвый+сломанный гибридный fallback;
  - `text_layer._is_name_embedded_number` — «Ca 125»: число в имени, не значение
    (устранён фантом «Са = 125.0»); парсеры строк объединены в `_parse_first_result`.
- **docs**: `HANDOFF.md` (промпт для агента без GPU), `TEST_RESULTS.md`, дате-фактура
  `habr/2026-06-27--ollama-speed-optimization.md`.
- **chore(bench)**: `scripts/bench/` + игнор bench-артефактов в `.gitignore`.
- **feat(llm) warmup**: `client.warmup()` грузит модель в VRAM на старте бота/API фоном
  (не блокирует), best-effort; первый документ не платит холодный старт ~100–120s.

### Результат
- Unit: **313 passed** (`uv run pytest -m "not llm"`), `ruff check src/ tests/` clean.
- E2E (реальная Ollama, оператор с GPU): **34/34 PASS, 325/325 (100%), 26.0s/док** (было 50.6),
  score 0.0369 (было 0.0172). Все 4 исходных FAIL (001/004/011/013) закрыты, регрессий нет.

### Следующий шаг
1. Ревью/push ветки `fix/ocr-stability-accuracy` (по команде оператора).
2. Открытый техдолг — в `HANDOFF.md` (раздел «Открытый техдолг»): HE4 unit/ref (пороги
   пре-/постменопаузы), остаточный двойной пустой ответ.
3. warmup замерить на GPU: подтвердить, что первый документ после старта не платит cold start.

### Как продолжить без локальной Ollama
Полный контекст и ограничения — в `HANDOFF.md`. Кратко: `uv run pytest -m "not llm"`,
LLM-логика через моки, реальных документов в репо нет (только `*.expected.json`).

---

## (история) Завершено ранее: OCR fallback для sample_006.pdf (Андрофлор)

### Что сделано
- `src/botkin/parsing/androflor.py` — добавлен детерминированный parser OCR-текста Андрофлор:
  - `Геномная ДНК человека: 10 5.7` → `value_num=5.7`, `unit=Lg`;
  - `Lactobacillus spp.: 10 4.7 -0.1 (68-91%)` → абсолютная строка `4.7 Lg` + относительная строка `-0.1 Lg(X/СВМО)`;
  - `не выявлено` не превращается в числовую строку.
- `src/botkin/llm/extract.py` — добавлен узкий OCR fallback для растровых таблиц:
  - если structured VLM-вызов падает `ExtractionError`/invalid JSON, пробуем простой OCR;
  - если VLM вернул Lg-артефакт `value_raw="10 4.8"` на Андрофлор-подобной строке, пробуем OCR;
  - OCR-результат принимается только если текст похож на Андрофлор (`андрофлор`, `lactobacillus spp`, `геномная днк человека`), затем парсится локально.
- `tests/test_androflor_parser.py` — добавлены unit-тесты parser'а и routing fallback.
- `habr/lab-results-journal.md` — добавлена Итерация 22 с диагнозом и результатами.

### Проверки
- Быстрый контур: `.venv\Scripts\ruff check src tests/test_androflor_parser.py`; `.venv\Scripts\python -m pytest tests/test_androflor_parser.py tests/test_text_layer_extract.py -q --no-header` → `12 passed`.
- Полный unit без LLM: `.venv\Scripts\ruff check src tests`; `.venv\Scripts\python -m pytest tests --ignore=tests/test_e2e_llm.py -q --no-header` → `284 passed in 28.98s`.
- Target e2e: `wsl -d Ubuntu -- .venv/Scripts/python.exe -m pytest "tests/test_e2e_llm.py::test_e2e_real_document_pipeline[sample_006.pdf]" -m llm --no-header -q -s --tb=short` → `sample_006.pdf PASS`, `20/20`, `extract 262.6s`, `1 passed in 272.51s`.
- Полный LLM e2e: `wsl -d Ubuntu -- .venv/Scripts/python.exe -m pytest tests/test_e2e_llm.py -m llm --no-header -q -s --tb=short` → **26 PASS / 8 FAIL**, `47.7 мин`, `classify 455.0s`, `extract 2409.1s`; `sample_006.pdf PASS 20/20`, `extract 250.2s`.

### Диагноз
- `sample_006.pdf` page1 — raster-only таблица Андрофлор, данных в text layer нет.
- Под structured JSON-промптом qwen3-vl нестабилен: иногда invalid JSON/пустой ответ, иногда `value_raw="10 4.8"` → общий `parse_lab_value` даёт `10.0`, иногда строки из описания/литературы.
- Простой OCR-промпт читает таблицу достаточно хорошо; устойчивое решение — OCR → детерминированный parser Андрофлор.

### Оставшиеся FAIL полного e2e
- `sample_008`: многострочные имена RDW/PDW/PCT.
- `sample_009`: 18/27, коллизии/timeout text-layer; появился captured log `Request timed out` на `analysis-text` page1.
- `sample_011`: 11/20, растровая/сложная таблица Тонус.
- `sample_012`: 46/47, один пропущенный гемоглобин 12.4 г/дл.
- `sample_016`: 35/63, СИБР-таблица 8×4.
- `sample_021/024/025`: ошибки doc_type JPG.

### Итог эксперимента: qwen2.5vl:7b не лучше qwen3-vl:8b (Итерация 23)

Скачана и протестирована `qwen2.5vl:7b`. Результаты на проблемных документах:
- `sample_006.pdf`: qwen3-vl + OCR fallback = **20/20 PASS**; qwen2.5vl + OCR fallback = **19/20 FAIL**.
- `sample_011.pdf`: обе модели = **11/20 FAIL**.

Вывод: смена VLM-модели в семействе Qwen **не решает** проблему. OCR fallback нужен при любой модели.
Возвращаемся к `qwen3-vl:8b-instruct`.

### Итог эксперимента: препроцессинг не лечит нестабильность (Итерация 24)

Прогнал structured extraction на цветной таблице Андрофлор (p1) в вариантах: baseline (CLAHE+JPEG),
PNG hi-res без CLAHE, grayscale. По 2 повтора, в т.ч. при `temperature=0.0`.

Главный факт: **при greedy (temp=0) один файл даёт 13 и 0 строк между повторами**. Нестабильность
в стеке обслуживания (GPU/grammar/таймауты), не в препроцессинге. Разброс между повторами больше,
чем между методами. Значения всё равно битые (Lg-артефакт `10.010`).

Вывод: grayscale/бинаризация/CLAHE — мифы классического OCR, для VLM не помогают. Препроцессинг —
не решение. Курс на детерминированный OCR-путь подтверждён.

### Итог замера 5 OCR-моделей (Итерация 25) — РЕШАЮЩИЙ

Прогнаны через Ollama в OCR-режиме (без grammar) на sample_006_p1 (Андрофлор) и sample_011_p1 (Тонус):

| модель | Андрофлор | Тонус | скорость |
|---|---|---|---|
| **qwen3-vl:8b** (прод) | все значения ✓ | весь ОАК ✓ | 74с/25с |
| qwen2.5vl:7b | ✓ | галлюцинация (153→15.5) | 77с/29с |
| glm-ocr (2.2 ГБ) | ✓ быстро | СРЫВ в бесконечный цикл | 36с/104с |
| minicpm-v4.5 | ~✓ но китайские иероглифы/think | ~✓ шум | 332с/59с |
| deepseek-ocr | МУСОР "т т т" | провал 121 симв | 46с/4с |

**Главный вывод: новая модель НЕ нужна.** qwen3-vl:8b (уже стоит) в OCR-режиме читает ОБЕ
проблемные таблицы чисто и с верными значениями. Проблема прод-пайплайна — в слое structured
output/grammar, а не в чтении. Остальные модели либо галлюцинируют (qwen2.5vl), либо срываются
(glm-ocr на Тонусе), либо медленны+шумят (minicpm), либо выдают мусор (deepseek-ocr).

Эмпирика побила бенчмарки: GLM-OCR по обзору SOTA, но на Тонусе ушёл в бесконечный цикл.

### Следующий шаг (архитектура подтверждена замером)
Сделать **OCR-путь на текущем qwen3-vl** (без grammar → текст → `_structure_text`/доменный parser)
ПЕРВИЧНЫМ для image-only плотных таблиц, а не аварийным fallback. Это:
- не требует нового веса в VRAM / новой зависимости / vLLM;
- использует то, что уже доказанно читает корректно;
- Андрофлор-парсер становится частным случаем общего OCR→structuring;
- потенциально закрывает sample_011 (Тонус) и другие растровые таблицы.

Скачанные для теста модели можно удалить: `ollama rm glm-ocr deepseek-ocr openbmb/minicpm-v4.5 qwen2.5vl:7b`.
Временные файлы теста (`_ocr_bench.py`, `_prep_experiment.py`, `_ocr_bench_out/`) удалены.

Эксперименты со сменой модели (qwen2.5vl) и препроцессингом завершены, временные скрипты удалены.
Прод-модель остаётся `qwen3-vl:8b-instruct`. qwen2.5vl:7b скачана в Ollama (можно удалить
`ollama rm qwen2.5vl:7b`).
