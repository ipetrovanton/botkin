# Результаты тестирования

Живой файл с результатами прогонов. Обновляется после значимых прогонов. Историю решений
и расследований см. в `habr/2026-06-27--ollama-speed-optimization.md`, план работы с
окружением без GPU — в `HANDOFF.md`.

---

## Прогон 2026-07-02 — стабилизация текстового пути (adaptive retry)

**Окружение:** Windows 11, нативная Ollama 0.30.11, RTX 3080 Laptop 16 GB,
модель `qwen3-vl:8b-instruct`. Стенд: 34 реальных документа (`tests/fixtures/documents/samples`).

### Unit-тесты (без GPU)
```
uv run pytest -m "not llm"   → 313 passed (309 базовых + 2 на adaptive retry + 2 на «число в имени»)
uv run ruff check src/ tests/ → All checks passed!
```

### E2E (реальная Ollama, `scripts/bench/bench_models.py`)

| Метрика | Baseline | Текущий | Изменение |
|---|---|---|---|
| PASS | 30/34 | **34/34** | +4 |
| FAIL | 4 | **0** | -4 |
| Точность (найдено/эталон) | 320/325 (98.5%) | **325/325 (100%)** | +1.5 п.п. |
| Среднее время/док | 50.6s | **26.0s** | **-49%** |
| Score = точн.×pass / время | 0.01718 | **0.03851** | **+2.2x** |

**FAIL: нет.** Все 4 исходных провала baseline (sample_001, 004, 011, 013) закрыты.

### Проверка стабильности sample_001 (6 прогонов run_analysis)

Причина прошлых плавающих FAIL — пустой ответ XGrammar на текстовом пути без ретрая.

| Конфигурация | Стабильность (3/3 из 6 прогонов) |
|---|---|
| qwen3-vl, без ретрая (было) | 3/6 |
| qwen3-vl + adaptive retry (1 попытка) | 5/6 |
| **qwen3-vl + adaptive retry (`_TEXT_EMPTY_RETRIES=2`, текущее)** | **6/6** |
| qwen3:8b (text-only) — отвергнута | 3/6 (непустой, но неверный вывод) |

### Подокументные тайминги (extract, сек)
```
sample_001  20.2 (3/3)    sample_011  68.5 (20/20)   sample_019  55.2 (25/25)
sample_002  15.0 (6/6)    sample_012  95.6 (47/47)   sample_020  29.5 (21/21)
sample_003  27.0 (11/11)  sample_013  72.6 (36/36)   sample_021–034 (JPG): classify-only, 3.9–5.2s
sample_004  18.1 (1/1)    sample_014  24.1 (13/13)
sample_006  76.6 (20/20)  sample_016  71.3 (63/63)
sample_008  33.0 (24/24)  sample_009  89.8 (27/27)
```

### Исправлено в этом прогоне
- **Мусорная строка `Антиген аденогенных раков Са = 125.0`** (число «125» из имени «Ca 125»
  читалось как значение). Фикс `_is_name_embedded_number` + 2 регрессионных теста. Проверено
  на реальных e2e: sample_001 3/3, sample_013 36/36 (multi-result pH цел).

### Известные не-блокирующие находки (техдолг)
- HE4 на sample_001: `unit`/`ref_high` прилипают к соседним токенам (140 вместо 70) —
  `field_mismatch`, не роняет тест. Детали — в `HANDOFF.md`.

### Как воспроизвести
```powershell
# Unit (без GPU):
uv run pytest -m "not llm"
# E2E (нужна Ollama + GPU):
uv run python scripts/bench/bench_models.py --models qwen3-vl:8b-instruct --skip-synthetic
uv run python scripts/bench/bench_compare.py
```
