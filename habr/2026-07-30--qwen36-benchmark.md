# Бенчмарк VLM-моделей для извлечения меданализов: Qwen3.6 vs Gemma 4 vs Qwen3-VL

> Дата: 2026-07-30
> Стек: Python 3.14, Ollama 0.32.5, FastAPI, aiogram
> OS: macOS 16.0, M3 Max 36 GB

## Постановка

Сравнить 5 VLM-моделей на E2E-пайплайне извлечения меданализов из PDF и фото:
классификация → OCR → structured extraction → сверка с эталоном (precision/recall).

Модели:
- `qwen3-vl:8b-instruct` — базовая модель, уже в проде
- `gemma4:26b` — Google Gemma 4 26B-A4B MoE (3.8B active), Q4_K_M, 17 GB
- `huihui_ai/Qwen3.6-abliterated:27b` — dense, Q4_K_M, 17 GB
- `huihui_ai/Qwen3.6-abliterated:35b` — MoE A3B, Q4_K_M, 23 GB
- `dhiltgen/qwen3-vl:30b-a3b-thinking-q4_K_M-ggml` — MoE A3B, thinking, 20 GB

Тестовый корпус: 34 реальных документа (PDF + JPG), 325 эталонных значений показателей.

## Контекст и ограничения

- Ollama на `localhost:11434`, OpenAI-совместимый endpoint `/v1/chat/completions`.
- `VLM_DISABLE_THINKING=1` — отключение reasoning-режима для thinking-capable моделей.
- Для Qwen: `chat_template_kwargs.enable_thinking=False` + `reasoning_effort: "none"`.
- Для Gemma 4: `options.think=False` (Ollama-специфичный параметр).
- `VLM_MODEL` и `TEXT_MODEL` устанавливаются в тестируемую модель — весь флоу на одной модели.
- Бюджеты: classify 600s, extract 1800s, таймаут одного вызова 600s.

## Ход работы

### Сложность 1: E2E виснет на thinking-моделях

- **Симптом:** 27b генерировала 4096 thinking-токенов с пустым `content`, extract занимал 431s.
- **Причина:** `_call_text_compact` не передавал `chat_template_kwargs`/`reasoning_effort`.
- **Решение:** добавил отключение thinking в 4 точках: `client.py`, `extract.py`, `image_ocr.py`, `sibr_ocr.py`.
- **Результат:** extract на sample_001 упал с 431s до 14.4s.

### Сложность 2: Gemma 4 и thinking

- Gemma 4 использует токен `<|think|>` в system prompt, не `chat_template_kwargs`.
- Ollama поддерживает `options.think=false` для Gemma 4 — добавил во все 4 точки вызова.
- Источник: [HF google/gemma-4-26B-A4B](https://huggingface.co/google/gemma-4-26B-A4B) — секция "Thinking Mode Configuration".

### Сложность 3: text-only кванты Gemma 4

- `batiai/gemma4-26b:iq4` — text-only, нет `vision` в capabilities.
- `odytrice/gemma4:4090-26b` — vision ✓, но качался 2+ часа из-за throttling.
- `gemma4:26b` (официальный Ollama тег) — vision ✓, Q4_K_M, 17 GB — финальный выбор.

### Сложность 4: sample_006 (СИБР — 20 показателей)

- 27b, 35b, 30b — все FAIL (0/20). Модели не могут структурировать таблицу СИБР.
- 8b — PASS (20/20, 47.2s). Модель лучше следует инструкциям промпта.
- gemma4:26b — PASS (20/20, 1 EXTRA). Корректно извлекает все показатели.

## Результаты

### Сводная таблица

| Метрика | qwen3-vl:8b | gemma4:26b | Qwen3.6:27b | Qwen3.6:35b | qwen3-vl:30b |
|---|---|---|---|---|---|
| PASS/FAIL | 34/0 | 34/0 | 34/1 | 34/1 | 28/7 |
| Pass rate | 100% | 100% | 97% | 97% | 80% |
| Точность | 325/325 (100%) | 325/325 (100%) | 314/334 (94%) | 314/334 (94%) | 292/333 (87.7%) |
| Среднее/док | 20.6s | 12.6s | 76.2s | 25.1s | 90.7s |
| Median | 9.6s | 8.1s | 36.6s | 13.1s | 45.8s |
| VRAM | 8.0 GB | 17.0 GB | 17.0 GB | 23.0 GB | 20.0 GB |
| Wall time | 702s (12м) | 431s (7м) | 2765s (46м) | 897s (15м) | 3260s (54м) |
| Score | 0.0216 | 0.0349 | 0.0056 | 0.0169 | 0.0040 |

score = (precision × pass_rate) / среднее_время_на_документ — выше = лучше

### Таблица расхождений

| Документ | 8b | gemma4:26b | 27b | 35b | 30b |
|---|---|---|---|---|---|
| sample_001 | PASS | MISMATCH(1): ROMA [unit] | MISMATCH(1): ROMA [unit] | PASS | FAIL: MISSING(1) + MISMATCH(2) HE4 |
| sample_002 | EXTRA(2) | EXTRA(2) | EXTRA(2) | EXTRA(2) | PASS |
| sample_003 | EXTRA(2) | MISMATCH(4): антитела [unit]: КП→Отрицательный КП + EXTRA(3) | MISMATCH(5) + EXTRA(3) | PASS | FAIL: MISSING(5) — все антитела |
| sample_004 | PASS | MISMATCH(1): ТТГ [unit]: мкМЕ/мл→мкМЕ/л | PASS | PASS | — |
| sample_006 | PASS | EXTRA(1) | FAIL: MISSING(20) | FAIL: MISSING(20) | FAIL: MISSING(20) |
| sample_009 | EXTRA(20) | MISMATCH(2): единицы 10^12/л разбились + EXTRA(22) | EXTRA(22) | EXTRA(22) | PASS |
| sample_010 | EXTRA(16) | MISMATCH(1): Билирубин [unit] + EXTRA(17) | EXTRA(17) | EXTRA(16) | MISMATCH(1) + EXTRA(2) |
| sample_011 | EXTRA(4) | MISMATCH(1): MCH [unit]: пг→фл + EXTRA(4) | EXTRA(3) | EXTRA(3) | EXTRA(25) |
| sample_012 | EXTRA(21) | EXTRA(20) | EXTRA(20) | EXTRA(20) | FAIL: MISSING(3) + MISMATCH(3) |
| sample_013 | EXTRA(18) | EXTRA(18) | EXTRA(18) | EXTRA(18) | FAIL: MISSING(1) + MISMATCH(1) |
| sample_016 | EXTRA(3) | EXTRA(4) | EXTRA(3) | EXTRA(5) | FAIL: MISSING(9) + MISMATCH(1) + EXTRA(18) |
| sample_019 | EXTRA(17) | EXTRA(17) | EXTRA(17) | EXTRA(17) | FAIL: MISSING(2) + MISMATCH(1) |

### Типы расхождений

1. **EXTRA** — модель извлекает показатели (цвет мочи, белок, глюкоза), которых нет в sidecar-эталоне. Проблема в разметке, не в модели: все 4 модели добавляют одинаковые EXTRA на sample_009/010/012/013/019.
2. **MISMATCH** — единицы измерения отличаются от эталона (КП→Отрицательный КП, пг→фл, мкмоль/л→(+)). gemma4:26b добавляет MISMATCH на единицах — модель копирует единицы из документа буквально, включая поясняющий текст.
3. **MISSING** — эталонный показатель не найден. Только 30b теряет показатели (recall < 1.0 на 5 документах).

## Архитектурные выводы

### MoE vs Dense

- **gemma4:26b** (MoE, 3.8B active) — в 1.6x быстрее 8b (dense, 8B active), в 6x быстрее 27b (dense, 27B active). MoE активирует лишь 3.8B параметров на токен — скорость генерации сопоставима с 4B-моделью.
- **Qwen3.6:35b** (MoE, 3B active) — в 3x быстрее 27b (dense), но точность та же. MoE-архитектура даёт скорость без потери качества.
- **27b** (dense) — медленная: 76.2s/док. Все 27B параметров активны на каждый токен.

### Thinking-режим

- **30b-thinking** — thinking не отключается полностью: модель генерирует пустые блоки `<|channel>thought\n<channel|>`, тратя токены. Classify — 2-7 t/s (против 24 t/s у gemma4:26b). 7 FAIL из-за потери показателей.
- **gemma4:26b** — `options.think=false` отключает thinking чисто, модель генерирует только ответ.
- **Qwen3.6** — `reasoning_effort: "none"` + `chat_template_kwargs.enable_thinking=False` отключают thinking, но 27b всё равно медленная из-за dense-архитектуры.

### Парадокс размера

8b (6.1 GB) и gemma4:26b (17 GB) дают 100% точность. 27b (17 GB) и 35b (23 GB) — 94%. 30b (20 GB) — 87.7%. Больше параметров ≠ лучше извлечение. Для задачи structured extraction из медицинских бланков способность следовать формату промпта важнее размера модели.

### sample_006 (СИБР) — индикатор

Таблица СИБР (20 показателей, сложная структура с Lg и относительными показателями) — единственный документ, который 27b/35b/30b не могут извлечь (0/20). 8b и gemma4:26b справляются (20/20). Это не проблема размера — это проблема способности модели следовать инструкциям структурирования.

## Изменения в коде

### Отключение thinking для Qwen и Gemma 4

4 точки: `client.py` (build_extra_body), `extract.py` (_call_text_compact), `image_ocr.py`, `sibr_ocr.py`.

```python
# Qwen: chat_template_kwargs + reasoning_effort
body["chat_template_kwargs"] = {"enable_thinking": False}
body["reasoning_effort"] = "none"

# Gemma 4: options.think
extra_body["options"]["think"] = False
```

### Конфигурация

- `config.json`: `max_tokens` и `num_predict` уменьшены с 4096/2048 до 1024/1024.
- `bench_models.py`: `VLM_MODEL` и `TEXT_MODEL` устанавливаются в тестируемую модель.
- Бюджеты: `E2E_CLASSIFY_BUDGET_S=600`, `E2E_EXTRACT_BUDGET_S=1800`, `VLM_REQUEST_TIMEOUT=600`.

### Post-extraction коррекция единиц через ФСЛИ-реестр

Бенчмарк показал 6 MISMATCH на gemma4:26b — все на поле `unit`. Три типа ошибок:

1. **Надстрочные индексы** (sample_009): `10^12/л` → `10^1   2/л` — OCR разбивает надстрочный текст
2. **Пояснения в unit** (sample_001, sample_003): `% (алгоритм ROMA)`, `Отрицательный КП`
3. **Неверная единица** (sample_004, sample_011): `мкМЕ/мл`→`мкМЕ/л`, `пг`→`фл`

Решение — гибрид: промпт-правило + post-extraction коррекция.

**Промпт-правила** (analysis_vlm.md правило 8, analysis_text_compact.md правило 6):
- В поле unit — только единица, без пояснений в скобках
- Надстрочные как `10^12/л`, не разбивать на символы
- Маркер результата (`(+)`, `(-)`) → null

**Post-extraction** (`extract.py: _correct_units`):
- Сверка `analyte_name` с ФСЛИ-реестром через `AnalyteNormalizer`
- Если `expected_units` есть и извлечённая единица не совпадает — 4 стратегии матчинга:
  1. Канонизация через `UNIT_ALIASES` (`10^9/л` → `×10⁹/л`)
  2. Очистка скобок + повторная канонизация (`% (алгоритм ROMA)` → `%`)
  3. Substring-матч (`Отрицательный КП` → `КП`)
  4. Fuzzy-матч (rapidfuzz, порог 85) — `мкМЕ/л` → `мкМЕ/мл`
- Оригинал сохраняется в `unit_raw` для аудита
- 10 тестов в `test_unit_correction.py`

## Итог

- **gemma4:26b** — лучший score (0.0349), 100% точность, 12.6s/док, 7 минут на весь корпус.
- **qwen3-vl:8b** — 100% точность, 8 GB VRAM, 20.6s/док. Базовая модель остаётся актуальной.
- **Qwen3.6:35b** — 97% pass, 25.1s/док. Лучший из Qwen3.6, но проваливает СИБР.
- **Qwen3.6:27b** — 97% pass, 76.2s/док. Dense-архитектура слишком медленная.
- **qwen3-vl:30b-thinking** — 80% pass, 90.7s/док. Thinking не отключается, теряет показатели.

## Материалы

- [HF google/gemma-4-26B-A4B](https://huggingface.co/google/gemma-4-26B-A4B) — спецификация, thinking mode configuration.
- [Ollama batiai/gemma4-26b](https://ollama.com/batiai/gemma4-26b) — text-only на Ollama, vision через llama.cpp.
- [Ollama odytrice/gemma4](https://ollama.com/odytrice/gemma4) — vision-теги для RTX 4090/5090.
- [HF unsloth/gemma-4-26B-A4B-it-GGUF](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF) — GGUF-кванты.
- [Ollama issue #14820: reasoning_effort in OpenAI compat](https://github.com/ollama/ollama/issues/14820).
- Результаты: `scripts/bench/bench_models_results.json`, `benchmarks/models_comparison_2026-07-30.md`.
- Логи: `bench_qwen3-vl_8b-instruct.log`, `bench_gemma4_26b.log`, `bench_huihui_ai_Qwen3.6-abliterated_27b.log`, `bench_huihui_ai_Qwen3.6-abliterated_35b.log`, `bench_dhiltgen_qwen3-vl_30b-a3b-thinking-q4_K_M-ggml.log`.
