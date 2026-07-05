# Текущий handoff

## 2026-07-05 — ветка feature/uncensored-med-llm: свежие uncensored-LLM + research-RAG + веб

**Цель:** изучить свежие локальные LLM без цензуры для медицины/русского, установить 5,
прогнать через RAG на тестовых анализах, дать доступ в интернет + RAG из мед.исследований,
оформить фактуру для Хабра. Железо: RTX 3080 16GB + 48GB RAM.

**Сделано (коммит d4fcb15, локально, НЕ запушено):**
- Выбраны 5 свежих моделей (теги проверены на ollama.com/tags):
  `huihui_ai/Qwen3.6-abliterated:27b`, `:35b-a3b`, `huihui_ai/glm-4.7-flash-abliterated:q4_K`,
  `goekdenizguelmez/JOSIEFIED-Qwen3:8b-health-q6_k`, `richardyoung/deepseek-r1-32b-uncensored`
  + бейслайн `qwen3:8b` (цензурный). GLM-4.6/5.2/Air НЕ влезают по железу.
- research-RAG из PubMed: `src/botkin/rag/research.py` + `scripts/update_medical_research.py`
  (source="research", идемпотентно по PMID). Уже проиндексировано **79 статей**.
- Веб-доступ моделей: `src/botkin/rag/websearch.py` (DuckDuckGo Lite + live PubMed) →
  augmentation контекста. `recommend(model=, use_web=)`; конфиг `rag.web_*`, `rag.research.*`.
- Миграция `rag_chunks`: CHECK(source) += 'research' (connection.py `_migrate_rag_chunks_schema`).
- Харнесс `scripts/bench/bench_uncensored_rag.py` (--web off/on/both) + анализ/графики
  `scripts/bench/analyze_uncensored_rag.py`. Дымовой тест на qwen3:8b прошёл (модель
  процитировала PubMed-абстракты). 397 тестов зелёные, ruff clean (мои файлы).

**Состояние: ЗАДАЧА ВЫПОЛНЕНА** (коммиты в feature/uncensored-med-llm, локально, НЕ запушено).
- Прогон завершён: 6 моделей × 3 вопроса (RAG) + веб-подвыборка (differential, все web_used=да).
  Результаты: habr/bench-uncensored/{results.json,md,analysis.md,chart_*.png},
  habr/bench-uncensored-web/. Битую richardyoung/deepseek заменили на
  huihui_ai/deepseek-r1-abliterated:8b-0528-qwen3.
- Ключевые выводы (в фактуре habr/2026-07-05--uncensored-med-llm.md): 0% отказов у всех;
  MoE 35b-a3b (137с) >> dense 27b (733с) на оффлоаде; JOSIEFIED-health подняла онкомаркеры
  CA 72-4/549; GLM-4.7 протекает китайскими иероглифами в RU; research-RAG реально цитируется.
- Фиксы по ходу: num_predict=4096 + фолбэк think=False (пустой content thinking-моделей);
  таймаут 1800с + max_retries=0; графики на PIL (kaleido не стартует Chromium на хосте).
- Возможное продолжение: полная матрица `--web both` по всем вопросам (сейчас веб только на
  differential ради времени); прод-интеграция веб-доступа (сейчас RAG_WEB_ENABLED=False).
- Автообновление research: `uv run python -m scripts.update_medical_research` (планировщик ОС).
- Демо-пользователь: telegram 113521070 → user_id=1 (318 анализов, отклонения: лимфоциты 40%,
  моноциты 11.7%, базофилы). Фактура: `habr/2026-07-05--uncensored-med-llm.md`.
- NB: 27b/32b пойдут с оффлоадом в RAM → медленно; если OOM — кванты поменьше.

---


## 2026-07-03 — ветка refactor/web-cabinet-quality: аудит + рефакторинг + редизайн кабинета

Ветка `refactor/web-cabinet-quality` (от `feature/web-cabinet`, которая = master + коммит
`f0afe7c` веб-кабинета). Три коммита этой сессии, **346 passed, ruff clean, не запушено**.

### Что сделано (детали — журнал Хабра, итерации 28–30)

**Итерация 28 — веб-баги (TDD, все под тестами):**
- stored XSS в `renderChart` (имя показателя из OCR → innerHTML) — `escapeHtml()` +
  тест-страж сканирует шаблоны;
- off-by-one `date_to` (`created_at < date(?,'+1 day')`);
- `dynamics`: exact-match по `COALESCE(canonical,name)` + LIKE-fallback (бот жив),
  `DESC`+reverse — свежие точки;
- race-guard `_req`-токены на все fetch; ожила 404-ветка `pickAnalyte`;
- `:key` по индексам; стадия `processing` в `stageDone` + фикс done-логики в HTML.
- Новый `tests/test_cabinet_web.py` — app.js гоняется в node (заглушка localStorage).

**Итерация 29 — находка: температуры LLM никогда не работали.**
`get_client(temperature=...)` игнорировал параметр; classify и structured-VLM шли на
дефолте Ollama. Теперь `CLASSIFY_TEMPERATURE`/`VLM_TEMPERATURE` прокинуты в options
(2 теста-стража в test_llm_calls.py). OCR-хардкод 0.0 не тронут (намеренный детерминизм).
**Перепроверить на GPU-прогоне корпуса — классификация теперь на 0.1, не на дефолте.**
Чистки: numbers.py, reconstruct_lines/source_text/_is_continuation_line, 3 мёртвых
исключения, BOT_POLLING_TIMEOUT.

**Итерация 30 — редизайн:**
- Golos Text variable (кириллица) заендорен в `web/vendor/fonts` (2 woff2);
- палитра «клиническая ночь» (#081311) / «дневной кабинет»; акценты teal/cyan/amber;
- новый логотип «значение в коридоре нормы» (старый fill=var(--brand-gradient) в SVG
  не работал вообще);
- сигнатура: мини-шкала нормы в строках показателей (`refPosition`, коридор 25–75%,
  8 TDD-кейсов); эмодзи-стрелки удалены;
- десктоп ≥900px: bottom-nav → боковая рельса; `:focus-visible`, aria-live тосты;
- проверено chrome headless скриншотами (mobile/desktop/light) на живом API с demo-БД.

**OCR-ресёрч 2026-07:** `docs/ocr-models-research-2026-07.md`. Кандидат №1 — **GLM-OCR**
(0.9B, RU, официально в Ollama, 1.86 стр/с против ~26 с/док). Рукопись — каскад с
Qwen3.6-27B. Следующий шаг: `ollama pull glm-ocr` → корпус 34 доков (нужен GPU).

### Сессия 2 (03.07, вечер): жизненный цикл документа + бенчи + деплой
- **Дедупликация** повторных загрузок: `documents.file_sha256` (+миграция), правило
  new_count >= old_count → новые побеждают (старый док удаляется целиком), иначе старые
  (новый удаляется). `dedupe_document()` в orchestrator (между персистом и extracted).
  6 тестов test_dedupe.py. Фронт: state='duplicate' в очереди при 404-поллинге.
  Ограничение: бот-поллинг дубликата дотекает до таймаута (push-уведомление приходит).
- **API**: GET /{id}/source (FileResponse, +HEIC), DELETE /{id}, POST delete-batch,
  POST /{id}/reparse (очистка + повторный pipeline; 409 без файла). 401 без заголовка.
- **WEB_DEBUG_USER_ID** (env): вход без заголовка для дебага/локалки (deps.py читает
  config динамически — тесты с reload работают).
- **Фронт**: go() — автозагрузка списков при входе на экран; чипы всех показателей
  на аналитике; режим выбора (чекбоксы, выбрать все, массовое удаление);
  карточка: Оригинал (fetch→blob) / Обновить / Удалить. CSS-урок: .btn-danger должен
  идти ПОСЛЕ .btn (шортхенд border перебивает border-color).
- **docs/ocr-models-research-2026-07.md Часть 2**: таблица бенчей 13 моделей
  (диск/VRAM/OmniDocBench с версиями/скорость/ru) + независимые исследования +
  «каверзные места» — для статьи «ожидания vs реальность».
- **docs/deploy-local-web.md**: инструкция выкатки в интернет (cloudflared/ngrok/
  VPS+Caddy/Tailscale, systemd, безопасность).
- Итог: **364 passed**, ruff clean. Журнал: итерация 31.

### Состояние
- Коммиты сессии 1 запушены; коммит сессии 2 — см. git log.
- На master НЕ смержен и сам `feature/web-cabinet` (PR не создавался).
- stash `remember-buffers-before-web-cabinet` — старые буферы .remember с master
  (вероятно, уже неактуальны — в ветке новее).

### Следующие шаги
1. Push ветки + PR (feature/web-cabinet → master, затем refactor поверх, или один PR).
2. GPU: e2e-прогон корпуса (регрессия от температур classify 0.1), замер warmup,
   бенч GLM-OCR vs qwen3-vl.
3. Техдолг HE4 sample_001 (см. HANDOFF.md) — по-прежнему открыт.
4. Демо-этап: аутентификации нет (IDOR через X-Telegram-User-Id) — зафиксировано
   в журнале ит. 28, чинить настоящей аутентификацией перед продом.

### Команды
- тесты: `.venv/bin/python -m pytest -m "not llm"` (346); линт: `.venv/bin/ruff check src tests`
- скриншоты: seed demo-БД → `SQLITE_PATH=... uvicorn botkin.api.app:app --port 8901` →
  `google-chrome --headless=new --screenshot=... --window-size=390,844 --virtual-time-budget=5000 http://127.0.0.1:8901/`
- токен для push: строка 3 в `/home/claude/token.txt` (`TOK=$(sed -n '3p' ...)`)
