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
