# Текущий handoff

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

### Состояние
- 3 коммита на `refactor/web-cabinet-quality`; НЕ запушено, PR нет.
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
