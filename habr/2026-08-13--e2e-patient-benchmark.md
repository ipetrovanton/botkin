# E2E benchmark Qwen 3.6 35B vs Gemma 4 26B QAT на реальных пациентских пакетах

> Дата начала: 2026-08-13
> Стек: Python 3.14, pytest, ruff, Ollama 0.32.9, Pydantic structured output

## Постановка

Сравнить две локальные модели — `huihui_ai/Qwen3.6-abliterated:35b-a3b` и `gemma4:26b-a4b-it-qat` — на реальных e2e-пакетах пациентов. Критерии: фактическая точность (labs, dates, medications, contradictions), проверяемость выводов через evidence IDs, отсутствие галлюцинаций, скорость и стабильность.

Каждая модель запускается изолированно, последовательно, с остановкой предыдущей. Не допускается параллельный запуск или несколько загруженных моделей.

## Контекст и ограничения

- Ollama 0.32.9 в WSL2, RTX 3080 Mobile 16 GB.
- `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_KEEP_ALIVE=-1`.
- Изначально сервис запущен без `OLLAMA_KV_CACHE_TYPE=q8_0` и `OLLAMA_FLASH_ATTENTION`, поэтому 23-гиговая Qwen частично уходит в CPU и идёт медленно.
- Пациентские пакеты строго разделены; Garmin есть только у Петрова Антона Игоревича.
- Данные не уходят в облако.

## Ход работы

### Шаг 1: детерминированный verified patient report renderer

Реализован `scripts/bench/summarize_patient.py`: агрегирует подтверждённый structured audit и рендерит Markdown без нового LLM-вызова. Каждое утверждение сопровождается evidence ID (`LAB:*`, `REP:*`, `MED:*`, `HLT:*`, `ACT:*`).

### Сложность: свободный LLM-prose теряет citations

- **Симптом:** `generate_e2e_report.py` с монолитным prompt на сыром FACT_PACKAGE выдал красивый текст, но citation scorer показал `citation_count=0`, `citation_ratio=0.0`. Модель проигнорировала пациентские анализы и написала только про Garmin.
- **Гипотеза:** модели легче уйти в общие рассуждения, чем строго цитировать каждое утверждение.
- **Решение:** отчёт строится детерминированно из verified summary; LLM используется только на втором этапе для «humanize» — переписывания уже проверенного текста связным языком.
- **Урок:** для медицинских данных free-form synthesis без enforced citations непригоден как единственный источник истины.

### Шаг 2: structured audit — обязательный value_num и осмысленные contradictions

Усилены prompt и Pydantic-схема:
- `value_num` стал обязательным для lab assertions.
- Contradiction требует непустого description и группы evidence IDs.
- Скорер нормализует даты и сравнивает лабораторные значения по exact numeric.

После этого Qwen прошёл аудит для Петрова А.И.: labs 9/9, dates 11/11, medications 8/8, contradictions 2/2, findings 5 с валидными evidence.

### Шаг 3: Gemma4-26B

Gemma оказалась значительно быстрее (~3×), но изначально:
- не выдала ни одного contradiction;
- разбила medication raw на `schedule`, хотя в пакете `schedule=null`.

Исправления:
- Prompt для contradictions стал категоричным: «Для каждой пары facts обязательно создай contradiction, даже если изменился только статус/наличие результата».
- Scorer medications стал сравнивать только `raw`, когда package `canonical`/`schedule` равны `null`.

После этого Gemma прошла аудит для Петрова А.И. и Петровой И.И.

### Шаг 4: humanize verified report

Идея пользователя — скормить детерминированный report модели и попросить «очеловечить» — сработала:
- **Qwen:** 10422 chars, 32 citations, `citation_ratio=0.64`. Сохранил структуру, метки **ФАКТ**/*ИНТЕРПРЕТАЦИЯ*/_ГИПОТЕЗА_, практически все evidence IDs.
- **Gemma:** 6053 chars, 16 citations, `citation_ratio=0.30`. Сжала текст, потеряла часть citations, допустила мелкую ошибку в дате рождения (`24.02.02.1993`).

### Сложность: Qwen на Саулиной слишком медленный

- **Симптом:** первый lab-batch 16 записей занял ~180 с; с 347 labs оценка ~70 мин.
- **Диагноз:** 23-гиговая Qwen в q4 не умещается в 16 GB VRAM, часть слоёв оффлоадится на CPU; сервис Ollama запущен без q8_0 KV-cache и flash attention.
- **Решение (с разрешения пользователя):** перезапустить Ollama с `OLLAMA_KV_CACHE_TYPE=q8_0` и `OLLAMA_FLASH_ATTENTION=1`; увеличить `lab_batch_size` с 16 до 32 и `num_ctx` с 8192 до 16384.
- **Урок:** для больших моделей на 16 GB VRAM конфигурация кеша решает скорость сильнее, чем batch size.

## Архитектурные решения

### Решение: детерминированный renderer вместо free-form summary

- **Альтернативы:** 1) free-form LLM на сыром пакете — высокий риск галлюцинаций и потери citations; 2) free-form LLM с жёстким prompt — лучше, но scorer показал 0 citations; 3) deterministic renderer — 100% проверяемый output.
- **Выбрано:** deterministic renderer + optional humanize поверх него. Критерий: фактическая корректность и механическая проверяемость.
- **Компромисс:** текст менее «литературный», без свободной медицинской интерпретации; interpretation layer отделён и явно маркирован.
- **Когда пересмотреть:** если появится LLM, который стабильно генерирует prose с построчными citations и проходит citation scorer.

### Сложность: Gemma на Саулиной галлюцинирует IDs и плодит assertions

- **Симптом:** при `lab_batch_size=16` Gemma сгенерировала 156 assertions вместо 16, включая несуществующие `LAB:sample_002:8…115`.
- **Диагноз:** у статической Pydantic-схемы не было `minItems`/`maxItems` и `enum` для `evidence_ids`; модель «достраивала» ID по паттерну.
- **Решение:** в `deep_model_benchmark.py` добавлена `batch_audit_json_schema(counts, allowed_evidence_ids)`, которая строит JSON Schema с точным числом элементов в каждом массиве и `enum` допустимых evidence IDs. Для findings оставлен `maxItems=5`.
- **Последствие:** Gemma перестала генерировать лишние объекты, выход сократился с ~29K до ~3K символов на batch.

## Итог

| Пациент | Модель | structured audit | total_ids | invalid_ids | citation_ratio | cited_lines/claim_lines |
|---|---|---|---|---|---|---|
| Петров А.И. | Qwen 35B-a3b | passed | 98 | [] | 0.643 | 36/56 |
| Петров А.И. | Gemma 26B-QAT | passed | 98 | [] | 0.303 | 10/33 |
| Петрова И.И. | Qwen 35B-a3b | passed | 13 | [] | 0.500 | 9/18 |
| Петрова И.И. | Gemma 26B-QAT | passed | 13 | [] | 0.615 | 8/13 |
| Саулина И.И. | Qwen 35B-a3b | passed | 347 | [] | 0.800 | 20/25 |
| Саулина И.И. | Gemma 26B-QAT | passed | 346 | [] | 0.412 | 7/17 |

- Все structured audit прошли без invalid IDs и без утечек Garmin/weather.
- **Qwen** стабильнее сохраняет citations при humanize; на большом пакете (Саулина) его citation ratio выше (`0.8` против `0.412`).
- **Gemma** быстрее на мелких пакетах, но на Саулиной потребовалась дополнительная схемная защита от галлюцинаций и динамический `num_predict=12288`.
- Humanize не добавляет новых фактов и не выдаёт `SRC`-цитаты; все отчёты прошли guard-проверки.

## Архитектурные решения

### Решение: детерминированный renderer вместо free-form summary

- **Альтернативы:** 1) free-form LLM на сыром пакете — высокий риск галлюцинаций и потери citations; 2) free-form LLM с жёстким prompt — лучше, но scorer показал 0 citations; 3) deterministic renderer — 100% проверяемый output.
- **Выбрано:** deterministic renderer + optional humanize поверх него. Критерий: фактическая корректность и механическая проверяемость.
- **Компромисс:** текст менее «литературный», без свободной медицинской интерпретации; interpretation layer отделён и явно маркирован.
- **Когда пересмотреть:** если появится LLM, который стабильно генерирует prose с построчными citations и проходит citation scorer.

### Решение: per-batch JSON Schema с точными размерами и enum evidence IDs

- **Альтернативы:** 1) статическая схема + надежда на prompt — Gemma генерирует лишние assertion; 2) пост-фильтрация по expected_ids — сложно восстановить при обрыве JSON; 3) per-batch схема с `minItems`/`maxItems`/`enum` — лучший контроль.
- **Выбрано:** вариант 3. `batch_audit_json_schema` собирает схему под каждый batch, ограничивая число объектов и допустимые evidence IDs.
- **Компромисс:** небольшой оверхед на генерацию схемы; требуется аккуратно обрабатывать findings (maxItems=5, без minItems).
- **Когда пересмотреть:** если перейдём к одному большому вызову вместо batch'ей — схема тогда будет на весь пакет.

### Решение: per-model конфигурация structured audit

- **Альтернативы:** 1) единый `audit_config()` — Qwen тратит много памяти, Gemma обрезает длинный выход; 2) одинаковый batch size — Gemma галлюцинирует на большом batch; 3) `model_audit_config(model)` выбирает `num_predict` и batch size под модель.
- **Выбрано:** вариант 3: Qwen — `num_predict=8192`, batch=32; Gemma — `num_predict=12288`, batch=16.
- **Компромисс:** конфигурация размазана по коду, но избавляет от перебора универсальных значений.
- **Когда пересмотреть:** при добавлении новой модели — параметризовать через JSON/YAML конфиг.

## Материалы

- Файлы результатов: `benchmarks/e2e_patient_audit_v2/` и `benchmarks/e2e_patient_reports_verified_v2/`.
- Изменённые файлы: `scripts/bench/structured_audit.py`, `scripts/bench/run_e2e_patient_audit.py`, `scripts/bench/deep_model_benchmark.py`, `tests/test_structured_audit.py`, `scripts/bench/bench_compare.py`.
- Системная настройка Ollama: drop-in `override.conf` с `OLLAMA_KV_CACHE_TYPE=q8_0` и `OLLAMA_FLASH_ATTENTION=1`.
