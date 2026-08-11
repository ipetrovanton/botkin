# Handoff: feat/queue-and-lifestyle-recs

**Ветка:** `feat/queue-and-lifestyle-recs` (от master; feat/pipeline-speed-accuracy влита в master ff)

**Коммиты:**
- `76f408f` — очередь VLM с видимой позицией (pipeline/queue.py, queue_position в /status, UI)
- `aad23de` — lifestyle-рекомендации uncensored-моделью (recommend_lifestyle, /api/rag/lifestyle, кнопка UI)
- статья habr/botkin-habr-article.md: цифры обновлены (13–14 с/док, 640+ тестов)

**Тесты:** 657 passed (613 unit + 44 GPU/LLM), ruff clean, node --check app.js clean.
GPU-gate пройден: 34/34 e2e документов + 10 reasoning-тестов, 46м 42с.

**Что сделано в этой сессии (2026-08-09):**
- pipeline/queue.py: LlmQueue поверх Semaphore(1), position()/snapshot(), тесты в test_llm_queue.py
- /api/documents/{id}/status → + queue_position, queue_waiting; app.js показывает «В очереди: N-й»
- rag/recommend.py: recommend_lifestyle() + _reports_context (диагнозы врача в контекст)
- llm/prompts/lifestyle_recommend.md: 4 раздела (образ жизни/нагрузки/препараты/взаимодействия)
- config: rag.lifestyle_model / RAG_LIFESTYLE_MODEL, default huihui_ai/Qwen3.6-abliterated:27b
- тесты: tests/test_lifestyle_recommend.py (4), фактура habr/2026-08-09--queue-and-lifestyle-recs.md

**Сделано дополнительно в этой сессии (2026-08-09):**
- `src/botkin/defaults.json` вынесены дефолты из `config.py`.
- `src/botkin/rag/context.py` — сборка контекста пациента отдельно от `recommend.py`.
- `src/botkin/web/app.js` разбит на модули documents/health/assistant/admin.
- `HANDOFF.md` актуализирован (613 passed, маркер `llm`).

**Следующий шаг:**
1. ✅ GPU-gate пройден (657 passed)
2. Merge feat/queue-and-lifestyle-recs → master, push
3. Техдолг: персистентность фоновых задач после рестарта; unknown-рецепты
