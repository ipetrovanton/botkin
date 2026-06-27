# Текущий handoff

## Завершено: P1–P6 (sample_008/009/012/016/021/024/025)

Все изменения НЕ закоммичены.

### Что сделано в этой сессии
- P1–P4: доработан text-layer путь (многострочные имена, скобки, дедуп по размерности).
- P5: создан специализированный СИБР-парсер (`src/botkin/parsing/sibr.py`), интегрирован в VLM/text-layer
  пути, улучшена детекция СИБР по газовым маркерам H2+CH4+O2.
- P6: OCR-аудит JPG выявил, что эталонные `.expected.json` для sample_021/024/025 не соответствуют
  реальному содержимому фото; разметка обновлена под содержимое.

### Результат
- `sample_008.pdf`: PASS 24/24
- `sample_009.pdf`: PASS 27/27
- `sample_012.pdf`: PASS 47/47
- `sample_016.pdf`: PASS 63/63
- `sample_021.jpg`: PASS (doctor_report)
- `sample_024.jpg`: PASS (unknown)
- `sample_025.jpg`: PASS (unknown)
- Unit-тесты: **309 passed**
- `ruff check src tests`: clean

### Файлы
- `src/botkin/parsing/rows.py` — `_unit_dimension` и `_merge_key` для дедупа по размерности.
- `src/botkin/parsing/sibr.py` — новый парсер СИБР.
- `src/botkin/llm/extract.py` — интеграция СИБР-парсера.
- `tests/test_sibr.py` — unit-тесты СИБР-парсера.
- `tests/test_e2e_llm.py` — name-aware matcher для СИБР.
- `tests/fixtures/documents/samples/sample_021.expected.json` — обновлено под doctor_report.
- `tests/fixtures/documents/samples/sample_024.expected.json` — обновлено под unknown (рецепт).
- `tests/fixtures/documents/samples/sample_025.expected.json` — обновлено под unknown (рецепт).
- `habr/lab-results-journal.md` — дописаны P1–P6.

### Следующий шаг
Полный прогон LLM e2e по всем реальным документам, чтобы убедиться, что нет новых регрессий.

---

## (история) Завершено ранее: OCR fallback для sample_006.pdf (Андрофлор)

### Что сделано
- `src/botkin/parsing/androflor.py` — добавлен детерминированный parser OCR-текста Андрофлор:
  - `Геномная ДНК человека: 10 5.7` → `value_num=5.7`, `unit=Lg`;
  - `Lactobacillus spp.: 10 4.7 -0.1 (68-91%)` → абсолютная строка `4.7 Lg` + относительная строка `-0.1 Lg(X/СВМО)`;
  - `не выявлено` не превращается в числовую строку.
- `src/botkin/llm/extract.py` — добавлен узкий OCR fallback для растровых таблиц:
  - если structured VLM-вызов падает `ExtractionError`/invalid JSON, пробуем простой OCR;
  - если VLM вернул Lg-артефакт `value_raw="10 4.8"` на Андрофлор-подобной строке, пробуем OCR;
  - OCR-результат принимается только если текст похож на Андрофлор (`андрофлор`, `lactobacillus spp`, `геномная днк человека`), затем парсится локально.
- `tests/test_androflor_parser.py` — добавлены unit-тесты parser'а и routing fallback.
- `habr/lab-results-journal.md` — добавлена Итерация 22 с диагнозом и результатами.

### Проверки
- Быстрый контур: `.venv\Scripts\ruff check src tests/test_androflor_parser.py`; `.venv\Scripts\python -m pytest tests/test_androflor_parser.py tests/test_text_layer_extract.py -q --no-header` → `12 passed`.
- Полный unit без LLM: `.venv\Scripts\ruff check src tests`; `.venv\Scripts\python -m pytest tests --ignore=tests/test_e2e_llm.py -q --no-header` → `284 passed in 28.98s`.
- Target e2e: `wsl -d Ubuntu -- .venv/Scripts/python.exe -m pytest "tests/test_e2e_llm.py::test_e2e_real_document_pipeline[sample_006.pdf]" -m llm --no-header -q -s --tb=short` → `sample_006.pdf PASS`, `20/20`, `extract 262.6s`, `1 passed in 272.51s`.
- Полный LLM e2e: `wsl -d Ubuntu -- .venv/Scripts/python.exe -m pytest tests/test_e2e_llm.py -m llm --no-header -q -s --tb=short` → **26 PASS / 8 FAIL**, `47.7 мин`, `classify 455.0s`, `extract 2409.1s`; `sample_006.pdf PASS 20/20`, `extract 250.2s`.

### Диагноз
- `sample_006.pdf` page1 — raster-only таблица Андрофлор, данных в text layer нет.
- Под structured JSON-промптом qwen3-vl нестабилен: иногда invalid JSON/пустой ответ, иногда `value_raw="10 4.8"` → общий `parse_lab_value` даёт `10.0`, иногда строки из описания/литературы.
- Простой OCR-промпт читает таблицу достаточно хорошо; устойчивое решение — OCR → детерминированный parser Андрофлор.

### Оставшиеся FAIL полного e2e
- `sample_008`: многострочные имена RDW/PDW/PCT.
- `sample_009`: 18/27, коллизии/timeout text-layer; появился captured log `Request timed out` на `analysis-text` page1.
- `sample_011`: 11/20, растровая/сложная таблица Тонус.
- `sample_012`: 46/47, один пропущенный гемоглобин 12.4 г/дл.
- `sample_016`: 35/63, СИБР-таблица 8×4.
- `sample_021/024/025`: ошибки doc_type JPG.

### Итог эксперимента: qwen2.5vl:7b не лучше qwen3-vl:8b (Итерация 23)

Скачана и протестирована `qwen2.5vl:7b`. Результаты на проблемных документах:
- `sample_006.pdf`: qwen3-vl + OCR fallback = **20/20 PASS**; qwen2.5vl + OCR fallback = **19/20 FAIL**.
- `sample_011.pdf`: обе модели = **11/20 FAIL**.

Вывод: смена VLM-модели в семействе Qwen **не решает** проблему. OCR fallback нужен при любой модели.
Возвращаемся к `qwen3-vl:8b-instruct`.

### Итог эксперимента: препроцессинг не лечит нестабильность (Итерация 24)

Прогнал structured extraction на цветной таблице Андрофлор (p1) в вариантах: baseline (CLAHE+JPEG),
PNG hi-res без CLAHE, grayscale. По 2 повтора, в т.ч. при `temperature=0.0`.

Главный факт: **при greedy (temp=0) один файл даёт 13 и 0 строк между повторами**. Нестабильность
в стеке обслуживания (GPU/grammar/таймауты), не в препроцессинге. Разброс между повторами больше,
чем между методами. Значения всё равно битые (Lg-артефакт `10.010`).

Вывод: grayscale/бинаризация/CLAHE — мифы классического OCR, для VLM не помогают. Препроцессинг —
не решение. Курс на детерминированный OCR-путь подтверждён.

### Итог замера 5 OCR-моделей (Итерация 25) — РЕШАЮЩИЙ

Прогнаны через Ollama в OCR-режиме (без grammar) на sample_006_p1 (Андрофлор) и sample_011_p1 (Тонус):

| модель | Андрофлор | Тонус | скорость |
|---|---|---|---|
| **qwen3-vl:8b** (прод) | все значения ✓ | весь ОАК ✓ | 74с/25с |
| qwen2.5vl:7b | ✓ | галлюцинация (153→15.5) | 77с/29с |
| glm-ocr (2.2 ГБ) | ✓ быстро | СРЫВ в бесконечный цикл | 36с/104с |
| minicpm-v4.5 | ~✓ но китайские иероглифы/think | ~✓ шум | 332с/59с |
| deepseek-ocr | МУСОР "т т т" | провал 121 симв | 46с/4с |

**Главный вывод: новая модель НЕ нужна.** qwen3-vl:8b (уже стоит) в OCR-режиме читает ОБЕ
проблемные таблицы чисто и с верными значениями. Проблема прод-пайплайна — в слое structured
output/grammar, а не в чтении. Остальные модели либо галлюцинируют (qwen2.5vl), либо срываются
(glm-ocr на Тонусе), либо медленны+шумят (minicpm), либо выдают мусор (deepseek-ocr).

Эмпирика побила бенчмарки: GLM-OCR по обзору SOTA, но на Тонусе ушёл в бесконечный цикл.

### Следующий шаг (архитектура подтверждена замером)
Сделать **OCR-путь на текущем qwen3-vl** (без grammar → текст → `_structure_text`/доменный parser)
ПЕРВИЧНЫМ для image-only плотных таблиц, а не аварийным fallback. Это:
- не требует нового веса в VRAM / новой зависимости / vLLM;
- использует то, что уже доказанно читает корректно;
- Андрофлор-парсер становится частным случаем общего OCR→structuring;
- потенциально закрывает sample_011 (Тонус) и другие растровые таблицы.

Скачанные для теста модели можно удалить: `ollama rm glm-ocr deepseek-ocr openbmb/minicpm-v4.5 qwen2.5vl:7b`.
Временные файлы теста (`_ocr_bench.py`, `_prep_experiment.py`, `_ocr_bench_out/`) удалены.

Эксперименты со сменой модели (qwen2.5vl) и препроцессингом завершены, временные скрипты удалены.
Прод-модель остаётся `qwen3-vl:8b-instruct`. qwen2.5vl:7b скачана в Ollama (можно удалить
`ollama rm qwen2.5vl:7b`).
