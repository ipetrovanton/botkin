2026-07-03 — Итерация 27 (веб-кабинет) завершена. Ветка fix/ocr-stability-accuracy.
- Фронтенд: SPA в src/botkin/web/ (index.html, styles.css, app.js, vendor/alpine.min.js 3.15.12).
- Бэкенд: 11 новых /api/* роутов (documents.py, analytics.py), StaticFiles mount в app.py.
- Репозитории: DocumentRepo.search/distinct_clinics/date_range/stats, LabRepo.distinct_analytes,
  ReportRepo.distinct_doctors/for_period.
- Баг-фикс: LOWER() для кириллицы в SQLite — create_function("lower",...) в get_conn().
- Тесты: 341 passed (316 + 25 новых), ruff clean.
- НЕ закоммичено, НЕ запушено.
- Следующий шаг: ревью/коммит по команде оператора. Live-сервер запущен на 127.0.0.1:8000.
