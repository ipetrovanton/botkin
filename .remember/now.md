2026-07-03 — Итерация 27 (веб-кабинет) завершена. Ветка fix/ocr-stability-accuracy.
- Фронтенд: SPA в src/botkin/web/ (index.html, styles.css, app.js, vendor/alpine.min.js 3.15.12).
- Бэкенд: 11 новых /api/* роутов (documents.py, analytics.py), StaticFiles mount в app.py.
- Репозитории: DocumentRepo.search/distinct_clinics/date_range/stats, LabRepo.distinct_analytes,
  ReportRepo.distinct_doctors/for_period.
- Баг-фикс: LOWER() для кириллицы в SQLite — create_function("lower",...) в get_conn().
- Тесты: 341 passed (316 + 25 новых), ruff clean.

2026-07-03 (6) — GPU-бенчмарк завершён и закоммичен (44ba1e6). Ветка refactor/web-cabinet-quality.
3 модели на корпусе 34 доков:
- qwen3-vl:8b-instruct: 100% (325/325), 34/34 PASS, 25.7с/док
- glm-ocr: 81.8% (248/303), 28/34 PASS, 13.7с/док
- qwen2.5vl:7b: 76.3% (248/325), 27/34 PASS, 27.3с/док

## Чекпоинт: Task 3 HE4 — RED-тест написан, фикс не начат
RED-тест в tests/test_text_layer_extract.py::test_parse_text_line_he4_takes_first_threshold_and_clean_unit.
Тест падает: unit='пмоль/л Cobas 6000...' вместо 'пмоль/л', ref_high=140 вместо 70.
Файл НЕ закоммичен. Следующий шаг: фикс в text_layer.py → GREEN → коммит.

2026-07-04 — Итерация 34: новые модели qwen3.5:9b и GLM-4.6V-Flash-9B. Ветка refactor/web-cabinet-quality.
Baseline 3 моделей на RTX 3080:
- qwen3-vl:8b-instruct: 100% (34/34), 25.4с/док — эталон
- GLM-4.6V-Flash-9B: 98.5% (31/34), 98.0с/док — новый кандидат №2
- qwen3.5:9b: 74.1% (26/34), 125.4с/док — провал (thinking mode ломает vision)
Оптимизация:
- qwen3.5:9b thinking off: не помогло (3/5 FAIL), модель не пригодна для vision/OCR через Ollama
- GLM-4.6V без structured output: 751.5с/док (7.7× медленнее), XGrammar критически важен
- XGrammar двойственный эффект: критичен для большинства, но теряет 1-2 значения на отдельных
qwen2.5vl:7b исключена из покрытия (76.3%, слишком слабая).
Обновлено: docs/ocr-models-research-2026-07.md (Часть 4), habr/lab-results-journal.md (итерация 34).
Добавлено: VLM_DISABLE_THINKING env в client.py (для оптимизационных прогонов).
367 passed, ruff clean. Следующий шаг: коммит.
- 2026-07-28 20:19 | feat/email-auth | Валидация expected vs sample: найдено ~12 серьёзных рассинхронов (023,024,025,028,029,030,031 и др.)
2026-08-03 17:12 | master | e2e bench qwen3-vl 34/34 100% 17.6s; gemma4:latest 27/34 88%; gemma4:26b 26/34 80%; journal it37
2026-08-03 19:08 | feat/pipeline-speed-accuracy | phase1+3 ship: long_side=1600 e2e 34/34 13.3s; glm-ocr reject
2026-08-04 10:17 | feat/pipeline-speed-accuracy | commit 56e6309 pipeline speed e2e 34/34 13.3s
2026-08-04 12:09 | feat/pipeline-speed-accuracy | 97ef735 phase4/5 reject; phase6/7 ship e2e 34/34 13.4s
2026-08-09 10:15 | feat/queue-and-lifestyle-recs | queue+lifestyle ship: 611 passed, ruff clean, статья обновлена (13-14с, 640+ тестов)
2026-08-09 10:41 | feat/queue-and-lifestyle-recs | f7a58ef live run: 404.5s 27b, 2 боевых бага (bge-m3 404, sqlite3.Row.get), 613 passed
2026-08-09 — refactor web/app.js cabinet() into four module factories (documents/health/assistant/admin). node --check passes. /tmp/refactor_app_js.py removed.
2026-08-09 11:15 | feat/queue-and-lifestyle-recs | 2609879 refactor: defaults.json, rag/context.py, app.js modules; 613 passed, ruff clean, node --check OK
