2026-07-03 — Итерация 27 (веб-кабинет) завершена. Ветка fix/ocr-stability-accuracy.
- Фронтенд: SPA в src/botkin/web/ (index.html, styles.css, app.js, vendor/alpine.min.js 3.15.12).
- Бэкенд: 11 новых /api/* роутов (documents.py, analytics.py), StaticFiles mount в app.py.
- Репозитории: DocumentRepo.search/distinct_clinics/date_range/stats, LabRepo.distinct_analytes,
  ReportRepo.distinct_doctors/for_period.
- Баг-фикс: LOWER() для кириллицы в SQLite — create_function("lower",...) в get_conn().
- Тесты: 341 passed (316 + 25 новых), ruff clean.
- НЕ закоммичено, НЕ запушено.
- Следующий шаг: ревью/коммит по команде оператора. Live-сервер запущен на 127.0.0.1:8000.

2026-07-03 — refactor/web-cabinet-quality (от feature/web-cabinet). Baseline: 341 passed, ruff clean. Web-аудит агентом готов: XSS в renderChart (app.js:287, innerHTML+analyte из OCR), IDOR (нет auth), off-by-one date_to vs created_at (repos.py:233), canonical↔name рассинхрон dynamics (repos.py:381/404), LIMIT 60 ASC теряет свежие, race conditions fetch, :key коллизии, стадия processing отсутствует в STAGE_PROGRESS (app.js:363). Ждём: агент-архитектор backend, агент OCR-ресёрч. План: рефакторинг → багфиксы TDD → UI/логотип → журнал.

## 14:30 | refactor/web-cabinet-quality
Создана ветка refactor/web-cabinet-quality, выполнена полная диагностика кода (выявлены IDOR, stored XSS, race conditions, off-by-one даты, коллизии `:key`, отсутствие тестов upload/IDOR), baseline 341 тест зафиксирован.
2026-07-03 (2) — refactor/web-cabinet-quality: фаза багфиксов+чисток готова, 345 passed, ruff clean. Сделано TDD: date_to off-by-one (repos.py search, date(?,'+1 day')), dynamics exact-канон+LIKE-fallback+DESC/reverse (5 тестов repo), XSS escapeHtml в renderChart, race-guard _req токены (docs/doc/reports/dynamics), 404-ветка pickAnalyte ожила, :key→индексы, processing в stageDone + фикс stageDone(recognizing) в HTML (5 тестов node, test_cabinet_web.py). Чистки: numbers.py+тест удалены, reconstruct_lines/source_text/_is_continuation_line удалены (тесты переписаны на open_pdf/_flat_lines), ConfigurationError/LLMError/DatabaseError удалены, get_client без мёртвого temperature, BOT_POLLING_TIMEOUT удалён. НАХОДКА: CLASSIFY_TEMPERATURE/VLM_TEMPERATURE никогда не действовали (мёртвый параметр) — теперь прокинуты в options (2 теста test_llm_calls). Осталось: UI/дизайн (task 4), журнал, OCR-отчёт (готов у агента), коммиты.

2026-07-03 (3) — сессия завершена: журнал ит. 28-30 дописан, docs/ocr-models-research-2026-07.md создан, remember.md переписан. 346 passed, ruff clean. 3 коммита на refactor/web-cabinet-quality (баги+чистка / редизайн / docs). Не запушено.

2026-07-03 (4) — сессия 2 завершена: дедуп (sha256+правило количества, 6 тестов), source/delete/batch/reparse API (8 тестов), WEB_DEBUG_USER_ID, фронт (go/чипы/чекбоксы/массовое удаление/оригинал-обновить-удалить, 3 node-теста), бенч-таблица 13 OCR-моделей в docs, deploy-local-web.md. 364 passed, ruff clean. Журнал ит. 31.

2026-07-03 (5) — bench_expectations.py (ожидания vs реальность, надстройка над bench_models, EXPECTATIONS из docs с URL, отчёт md+json+консоль, 3 теста) + docs/devin-prompt.md (автономный GPU-прогон + фактура для Хабра). 367 passed, ruff clean. Журнал ит. 32.

2026-07-03 (6) — GPU-бенчмарк завершён. Ветка refactor/web-cabinet-quality, HEAD 07c49dc. Железо: RTX 3080 Laptop 16GB, Ollama 0.31.1. 3 модели прогнаны на корпусе 34 доков:
- qwen3-vl:8b-instruct: 100% (325/325), 34/34 PASS, 25.7с/док, 895с
- glm-ocr: 81.8% (248/303), 28/34 PASS, 13.7с/док, 703с
- qwen2.5vl:7b: 76.3% (248/325), 27/34 PASS, 27.3с/док, 995с
Обновлено: docs/ocr-models-research-2026-07.md (Часть 3 «Реальность»), habr/lab-results-journal.md (итерация 33). Отчёт: scripts/bench/bench_expectations_report.md. 367 passed, ruff clean. Следующий шаг: коммит, затем Task 3 (HE4 на sample_001) если останется время.
