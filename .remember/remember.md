# Handoff: feat/queue-and-lifestyle-recs

**Ветка:** `feat/queue-and-lifestyle-recs` (от master; feat/pipeline-speed-accuracy влита в master ff)

**Коммиты:**
- `76f408f` — очередь VLM с видимой позицией (pipeline/queue.py, queue_position в /status, UI)
- `aad23de` — lifestyle-рекомендации uncensored-моделью (recommend_lifestyle, /api/rag/lifestyle, кнопка UI)
- статья habr/botkin-habr-article.md: цифры обновлены (13–14 с/док, 640+ тестов)

**Тесты:** 611 passed (`uv run pytest -m "not llm" -k "not test_e2e_reasoning" -q`), ruff clean.
На Mac test_e2e_reasoning виснет без Ollama — исключать через -k.

**Что сделано в этой сессии (2026-08-09):**
- pipeline/queue.py: LlmQueue поверх Semaphore(1), position()/snapshot(), тесты в test_llm_queue.py
- /api/documents/{id}/status → + queue_position, queue_waiting; app.js показывает «В очереди: N-й»
- rag/recommend.py: recommend_lifestyle() + _reports_context (диагнозы врача в контекст)
- llm/prompts/lifestyle_recommend.md: 4 раздела (образ жизни/нагрузки/препараты/взаимодействия)
- config: rag.lifestyle_model / RAG_LIFESTYLE_MODEL, default huihui_ai/Qwen3.6-abliterated:27b
- тесты: tests/test_lifestyle_recommend.py (4), фактура habr/2026-08-09--queue-and-lifestyle-recs.md

**Следующий шаг:**
1. Живой прогон lifestyle на GPU: `curl -X POST localhost:8000/api/rag/lifestyle -H "X-Telegram-User-Id: <id>"`
   (нужна Ollama + huihui_ai/Qwen3.6-abliterated:27b)
2. e2e-прогон 34 доков на GPU для gate перед merge в master
3. Merge feat/queue-and-lifestyle-recs → master, push
4. Техдолг: персистентность фоновых задач после рестарта; unknown-рецепты
