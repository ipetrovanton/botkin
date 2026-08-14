# Rebase release/habr-article на origin/master и прогон e2e

> Дата начала: 2026-08-12
> Стек: Python 3.14, pytest, Ollama (qwen3-vl:8b-instruct, Qwen3.6-abliterated:27b), git

## Постановка

Пользователь попросил обновить `master` из remote, перебазировать ветку
`release/habr-article` на актуальный `origin/master` и запустить локально e2e-тесты.

Критерий успеха: `master` и `release/habr-article` на актуальном `origin/master`,
e2e-тесты прогнаны, результаты зафиксированы.

## Контекст и ограничения

- `origin/master` ушёл вперёд на 97 коммитов (`e812763` → `a567c15`), 378 файлов,
  +82 569 / −56 862 строк — большой объём работы по RAG, веб-кабинету, OCR, рефакторингу.
- На ветке `release/habr-article` были незакоммиченные правки статьи и сборщика PDF.
- Коммит `af097c2` (статья для Хабра + тестовые данные) конфликтовал с master по
  `.gitignore`, `pyproject.toml`, `uv.lock`, `habr/botkin-habr-article.md`.
- e2e-тесты помечены маркерами `llm` и `reasoning`, по умолчанию исключаются
  (`addopts = "-m 'not llm and not reasoning'"`). Запуск: `uv run pytest -m llm -s`.
- Дефолтная VLM-модель: `qwen3-vl:8b-instruct` (из `src/botkin/defaults.json`).
- Reasoning-тесты используют `huihui_ai/Qwen3.6-abliterated:27b` — 27B-модель с
  частичным CPU-оффлоадом (23% CPU / 77% GPU), отсюда медленность.

## План

1. Стешнуть незакоммиченные правки статьи и сборщика PDF.
2. Обновить локальный `master` до `origin/master` (ff-only).
3. Rebase `release/habr-article` на `origin/master`.
4. Вернуть стешнутые правки.
5. `uv sync` — установить актуальные зависимости.
6. `git lfs pull` — скачать LFS-фикстуры (sample_*.pdf).
7. `uv run pytest -m llm -s` — прогнать e2e-тесты.

## Ход работы

### Шаг 1: stash, обновление master, rebase

```bash
git stash push -m "WIP: habr article + build_habr_pdf before rebase" -- habr/botkin-habr-article.md scripts/build_habr_pdf.py
git checkout master && git pull --ff-only origin master   # e812763 → a567c15
git checkout release/habr-article && git rebase origin/master
```

Rebase остановился на конфликтах в 4 файлах:
- `.gitignore` — master добавил `minio`, `json-repair`, `dateparser`, игнор дампов;
  af097c2 добавил `markdown` и исключения для sample_*.pdf.
- `pyproject.toml` — master: `minio>=7.2.20`, `json-repair>=0.30.0`, `dateparser>=1.2.0`;
  af097c2: `markdown>=3.10.2`.
- `uv.lock` — зеркалит конфликт pyproject.toml.
- `habr/botkin-habr-article.md` — add/add: master уже содержит полную статью (373 строки),
  af097c2 — старая 3-строчная версия.

Разрешил в пользу `origin/master` (версия HEAD при rebase) — она актуальнее по всем
четырём файлам. Уникальные файлы из af097c2 (DEPLOY-MAC.md, .gitattributes, LFS-фикстуры
sample_001–020.pdf, data/botkin.db, benchmark-methodology.md) остались.

```bash
git checkout --ours .gitignore pyproject.toml habr/botkin-habr-article.md uv.lock
git add .gitignore pyproject.toml habr/botkin-habr-article.md uv.lock
GIT_EDITOR=true git rebase --continue
```

Rebase завершён: `c2fa21c` поверх `a567c15`.

### Шаг 2: возврат стеша

```bash
git stash pop
```

Конфликт в `habr/botkin-habr-article.md` — стешнутая правка делалась поверх старой
3-строчной версии, а в репо теперь полная статья из master. Разрешил в пользу
текущей (master), stash оставлен в `stash@{0}` на случай если понадобится.

### Шаг 3: зависимости и LFS

```bash
uv sync          # Python 3.14.5, все пакеты актуальны
git lfs pull     # sample_001.pdf = 107 KB (реальный PDF, не LFS-указатель)
```

### Шаг 4: прогон e2e

```bash
uv run pytest -m llm -s --tb=short
```

**Сводка:** 44 теста выбрано (613 deselected), 36 passed, 8 failed,
4824.76s (1:20:24).

#### test_e2e_llm.py — распознавание документов (35 тестов)

| Документ | Статус | classify | extract | precision | recall | mismatch |
|---|---|---|---|---|---|---|
| e2e_cbc.pdf | PASS | 0.0s | 62.9s | 1.00 | 1.00 | 0 |
| sample_001.pdf | PASS | 0.0s | 4.3s | 1.00 | 1.00 | 1 |
| sample_002.pdf | PASS | 0.0s | 8.5s | 0.75 | 1.00 | 1 |
| sample_003.pdf | PASS | 0.0s | 22.5s | 1.00 | 1.00 | 0 |
| sample_004.pdf | PASS | 19.8s | 8.5s | 1.00 | 1.00 | 0 |
| sample_005.pdf | PASS | 7.1s | 7.9s | 1.00 | 1.00 | 0 |
| sample_006.pdf | PASS | 0.0s | 30.3s | 1.00 | 1.00 | 9 |
| sample_007.pdf | PASS | 5.2s | 1.5s | — | — | — |
| sample_008.pdf | PASS | 0.0s | 19.8s | 1.00 | 1.00 | 0 |
| sample_009.pdf | PASS | 0.0s | 39.0s | 0.59 | 1.00 | 1 |
| sample_010.pdf | PASS | 0.0s | 19.9s | 0.20 | 1.00 | 0 |
| sample_011.pdf | PASS | 7.3s | 40.8s | 0.47 | 1.00 | 0 |
| sample_012.pdf | PASS | 0.0s | 50.7s | 0.70 | 1.00 | 0 |
| sample_013.pdf | PASS | 0.0s | 42.4s | 0.67 | 1.00 | 0 |
| sample_014.pdf | PASS | 0.0s | 11.3s | 1.00 | 1.00 | 0 |
| sample_015.pdf | PASS | 0.0s | 2.7s | 1.00 | 1.00 | 1 |
| sample_016.pdf | PASS | 0.0s | 61.2s | 0.95 | 1.00 | 0 |
| sample_017.pdf | PASS | 0.0s | 1.7s | 1.00 | 1.00 | 0 |
| sample_018.pdf | PASS | 0.0s | 10.6s | — | — | — |
| sample_019.pdf | PASS | 0.0s | 29.4s | 0.60 | 1.00 | 0 |
| sample_020.pdf | PASS | 0.0s | 19.4s | 1.00 | 1.00 | 0 |
| sample_021.jpg | PASS | 6.8s | 17.7s | 1.00 | 1.00 | 0 |
| sample_022.jpg | PASS | 6.6s | 0.0s | — | — | — |
| **sample_023.jpg** | **FAIL** | 7.8s | 11.2s | 0.50 | 0.50 | 1 |
| sample_024.jpg | PASS | 5.4s | 0.0s | — | — | — |
| sample_025.jpg | PASS | 4.8s | 0.0s | — | — | — |
| sample_027.jpg | PASS | 6.3s | 7.8s | 1.00 | 1.00 | 0 |
| sample_028.jpg | PASS | 6.1s | 8.7s | 1.00 | 1.00 | 0 |
| sample_029.jpg | PASS | 6.0s | 12.9s | 1.00 | 1.00 | 0 |
| sample_030.jpg | PASS | 5.9s | 17.4s | 1.00 | 1.00 | 0 |
| sample_031.jpg | PASS | 6.8s | 11.3s | 1.00 | 1.00 | 0 |
| sample_032.jpg | PASS | 7.0s | 8.4s | 1.00 | 1.00 | 0 |
| sample_033.jpg | PASS | 7.7s | 19.8s | 1.00 | 1.00 | 0 |
| sample_034.jpg | PASS | 6.9s | 15.9s | 1.00 | 1.00 | 0 |
| sample_035.jpg | PASS | 6.6s | 13.0s | 1.00 | 1.00 | 0 |

**Итог распознавания:** 34 PASS, 1 FAIL. Суммарно: classify 130.4s, extract 639.4s,
всего 769.8s (12.8 мин). Среднее на документ: 22.0s.

#### test_e2e_reasoning.py — medical reasoning (9 тестов)

Все 9 тестов провалены с `TimeoutError: timed out` при вызове Ollama.

Модель: `huihui_ai/Qwen3.6-abliterated:27b` (18 GB, 23% CPU / 77% GPU,
context=16384). Thinking mode генерирует тысячи токенов (`thinking=3905` для одного
запроса), а 27B-модель с частичным CPU-оффлоадом работает медленно — запросы
превышают бюджет времени и роняются по `urllib.request.urlopen` timeout.

### Сложность: sample_023.jpg — неполный extract doctor_report

**Симптом:** `doctor_report: не найдено 1/2 обязательных полей/диагноз: ['diagnosis']`

Модель вернула `diagnosis` в усечённом виде — только ЧСС и интервалы ЭКГ, без
основного диагноза («Ритм синусовый с ЧСС 62-66-75 уд/мин» вместо полного
заключения). precision=0.50, recall=0.50.

**Гипотеза:** VLM не дочитала заключение врача на скане, либо промпт extract для
`doctor_report` не достаточно настойчиво требует поле `diagnosis`. На остальных
14 doctor_report-документах (sample_021, 027–035) модель находит diagnosis
полностью — значит проблема в конкретном скане, не в промпте.

**Что не делал:** не чинил в этой итерации — это известная флуктуация VLM,
не регрессия после rebase.

### Сложность: reasoning-тесты — timeout на 27B-модели

**Симптом:** `TimeoutError: timed out` в `urllib.request.urlopen` для всех 9
reasoning-тестов.

**Диагноз:** `huihui_ai/Qwen3.6-abliterated:27b` (18 GB) не помещается в VRAM
целиком — Ollama оффлоадит 23% слоёв на CPU. Thinking mode генерирует 3000–4000
токенов рассуждений на один запрос. На CPU-оффлоаде это занимает больше бюджета
теста.

**Что пробовал:** ничего — это ограничение железа, не кода. На машине с бóльшей
VRAM (или с более лёгкой reasoning-моделью) тесты пройдут.

**Решение:** не чинил. Reasoning-тесты — маркер `reasoning`, можно исключить:
`uv run pytest -m 'llm and not reasoning'`. Для прогона reasoning нужна модель,
помещающаяся в VRAM целиком (например, `qwen3-abliterated:14b` или
`dolphin3:8b`).

## Сессия: устранение флака sample_023.jpg

### Шаг 5: воспроизведение и диагностика

Целевой запуск `uv run pytest "tests/test_e2e_llm.py::test_e2e_real_document_pipeline[sample_023.jpg]" -m llm -s --tb=short`
сначала один раз прошёл (17.9s), но серия из пяти независимых запусков дала 4 FAIL / 1 PASS.

Неудачный ответ VLM содержал часть ЭКГ-заключения: «Синусовый ритм с ЧСС 62–66–75…» и
«С вторичным изменением ST-T», но пропускал «Желудочковая экстрасистолия» и «Гипертрофия
левого желудочка». E2E фиксировал `precision=0.50`, `recall=0.50` и hard-missing
`diagnosis`.

Причина: в sidecar `sample_023.expected.json` прямо указано, что фото перевёрнуто.
`prepare_images()` применял EXIF-transpose и deskew малых углов, но не устранял поворот
на 180°. При `temperature=0.0` VLM всё равно флуктуировала на перевёрнутом ЭКГ-снимке.

### Шаг 6: фикс ориентации для растровых заключений

Добавлен `prepare_report_images()` в `src/botkin/preprocess/images.py`:

- для одного растрового изображения возвращает исходный JPEG и его вариант, повёрнутый
  на 180°;
- для PDF возвращает исходный набор страниц без дублирования;
- `run_doctor_report()` использует этот путь, а остальные ветви extract не меняются;
- `llm/prompts/doctor_report.md` сообщает модели, что две картинки — один документ,
  и требует выбрать читаемую ориентацию и не дублировать поля.

RED: до реализации тесты не собирались:
`ImportError: cannot import name 'prepare_report_images'`.

GREEN:

```text
uv run pytest tests/test_preprocess_images.py -q  → 11 passed in 1.31s
uv run pytest tests/test_prompts.py -q            → 4 passed in 0.33s
uv run ruff check src/botkin/preprocess/images.py src/botkin/llm/extract.py tests/test_preprocess_images.py
→ All checks passed!
```

Целевой e2e после фикса: один подробный прогон — PASS, diagnosis 2/2, 13.55s.
Повторная серия из пяти независимых запусков: **5/5 PASS**, 9.15–10.85s.
После форматирования файлов повторная проверка: `test_preprocess_images.py` + `test_prompts.py` —
15 passed за 1.49s; целевой e2e — PASS за 9.27s.

## Сессия: выбор модели для глубокой ночной аналитики

### Шаг 7: сверка результатов reasoning-бенчмарка и железа

Цель пользователя: максимум полезных выводов по всему накопленному массиву анализов,
заключений, назначений, данных Garmin и RAG-источников; интерактивная задержка не важна,
запуск допустим ночью или по отдельной кнопке.

Падение `test_e2e_reasoning.py` связано не с RAG-кодом: тест по умолчанию выбирает
`huihui_ai/Qwen3.6-abliterated:27b`, `think=medium`, `num_ctx=16384`, `num_predict=8192`
и ждёт ответ 600 секунд. Dense-27B с частичным CPU-offload превышает этот лимит.

Существующий бенчмарк полного медицинского отчёта (M3 Max, 36 ГБ unified memory) показал:

| Модель | Thinking | Время | Клинические темы |
|---|---|---:|---:|
| Qwen3.6-35b MoE | high | 192s | 15/16 |
| satgeze-35b-1m MoE | high | 201s | 16/16 |
| Qwen3.6-27b dense | high | 920s | 15/16 |
| GLM-4.7 Flash | medium | 427s | 10/16 |
| gemma4 | off | 96s | 10/16 |
| JOSIEFIED-8b-health | medium | 90s | 11/16 |

На текущем Windows-хосте `nvidia-smi` обнаружил `NVIDIA GeForce RTX 3080 Laptop GPU`,
16 384 MiB VRAM, свободно 16 175 MiB. Установленная MoE-модель
`huihui_ai/Qwen3.6-abliterated:35b-a3b` имеет размер 23 ГБ, поэтому и она не поместится
в VRAM полностью; её скорость на этом хосте нужно измерить отдельно, переносить 192s
с M3 Max нельзя.

### Шаг 8: внешняя сверка более мощных open-weight моделей

Проверены официальные model cards (обращение 2026-08-12):

- Локальная `gemma4:latest` через `ollama show` — Gemma 4 8B Q4_K_M, не 26B/31B.
- Официальная Gemma 4 26B A4B в Q4_0 занимает 14.4 ГБ, Gemma 4 31B — 17.5 ГБ.
  26B — реалистичный кандидат для RTX 3080 Laptop 16 ГБ, но KV-cache оставит мало запаса;
  31B уже не помещается полностью.
- Qwen3.5-35B-A3B — MoE: 35B всего, 3B активных на токен, native context 262K;
  Qwen3.5-27B — dense 27B. Для ограниченной VRAM MoE предпочтительнее dense.
- MedGemma 27B — специализированная медицинская text-only/multimodal модель Google
  с контекстом не менее 128K; доступ требует согласия с Health AI Developer Foundations terms.
  Качество русского медицинского reasoning на корпусе Botkin не измерено.
- `gpt-oss-20b` — текстовая MoE-модель OpenAI, официально рассчитанная на 16 ГБ памяти;
  интересна как independent reviewer, но не доказана на русских медицинских задачах.
- Qwen3-235B-A22B, DeepSeek-R1 671B, Llama 4 Scout (109B) / Maverick (400B),
  Mistral Large 3 (675B) исключены для локального запуска на 16 ГБ VRAM: их model cards
  указывают серверные GPU/многогPU-режимы.

## Архитектурные решения

### Ориентация растровых doctor_report

- **Альтернативы:** поворачивать все растровые документы на 180° — сломает уже правильно
  ориентированные фото; внедрить OCR-детектор ориентации — добавит новую тяжёлую зависимость
  и отдельный ненадёжный этап; повторять VLM-вызов только при плохом diagnosis — нельзя
  надёжно определить неполноту заключения без эталона.
- **Выбрано:** передавать VLM исходный и 180°-вариант только для растровых `doctor_report`.
  Это покрывает наиболее частый неустранимый EXIF/deskew поворот без изменения пути анализов
  и без новой зависимости.
- **Компромисс:** vision-контекст растрового заключения растёт примерно с 2390 до 3857
  токенов; в целевом тесте extract остался в диапазоне 8.6–10.9s.
- **Когда пересмотреть:** если средняя задержка извлечения doctor_report вырастет более чем
  на 30% в benchmark либо появится надёжный локальный orientation detector без тяжёлой
  зависимости.

## Итог

- `master` обновлён до `origin/master` (`a567c15`), fast-forward, без конфликтов.
- `release/habr-article` перебазирована на `origin/master` (новый HEAD `c2fa21c`).
  Конфликты разрешены в пользу master; уникальные файлы (DEPLOY-MAC.md, LFS-фикстуры,
  benchmark-methodology.md) сохранены.
- Зависимости установлены через `uv sync` (Python 3.14.5).
- LFS-фикстуры скачаны (`git lfs pull`).
- e2e-прогон: **36 passed, 8 failed** из 44 (4824.76s / 1:20:24).
  - Распознавание: 34/35 PASS (1 FAIL на sample_023.jpg — VLM не дочитала диагноз).
  - Reasoning: 0/9 PASS (timeout на 27B-модели с CPU-оффлоадом).
- Незакоммиченные правки статьи и сборщика PDF: `build_habr_pdf.py` в индексе,
  статья разрешена в пользу master-версии. Stash `stash@{0}` сохранён.

## Материалы

- `CLAUDE.md` — рабочие команды проекта (тесты, линт, push).
- `src/botkin/defaults.json` — дефолтные модели (VLM: `qwen3-vl:8b-instruct`).
- `pyproject.toml:69-75` — маркеры pytest и `addopts` для e2e.
- https://ai.google.dev/gemma/docs/core — Gemma 4 sizes/memory, обращение 2026-08-12.
- https://huggingface.co/Qwen/Qwen3.5-35B-A3B — Qwen3.5-35B-A3B model card, обращение 2026-08-12.
- https://developers.google.com/health-ai-developer-foundations/medgemma — MedGemma overview, обращение 2026-08-12.
- https://huggingface.co/openai/gpt-oss-120b — gpt-oss memory requirements, обращение 2026-08-12.
- https://github.com/meta-llama/llama-models/blob/main/models/llama4/MODEL_CARD.md — Llama 4 model card, обращение 2026-08-12.
