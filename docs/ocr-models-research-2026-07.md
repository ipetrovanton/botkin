# Веб-ресёрч: новые OCR/VLM для распознавания медицинских документов

*Дата: 2026-07-03. Метод: SearXNG + чтение первоисточников; каждый факт с URL.
Контекст: текущий стек — Ollama + qwen3-vl (гибрид: текстовый слой pymupdf + VLM),
100% на корпусе из 34 доков, ~26 с/документ.*

## Что изменилось после раздела 13 research-отчёта

С конца 2025 рынок сместился к **компактным специализированным document-VLM (0.6–4B)**,
которые на бенчмарках document parsing обходят гигантские универсальные модели (включая
Qwen3-VL-235B и Gemini-3-Pro). Новинки, которых не было в отчёте: **GLM-OCR** (02.2026),
**DeepSeek-OCR 2** (01.2026), **Qwen3.5/3.6** (02/04.2026), **MinerU2.5-Pro** (05.2026),
**Unlimited-OCR** от Baidu (06.2026), **Chandra 2** и **Surya 2** (Datalab),
**HunyuanOCR** (11.2025).

## Сводная таблица

| Модель | Версия / дата | Размер | RU? | Таблицы? | Скорость | Локально | Источник |
|---|---|---|---|---|---|---|---|
| **GLM-OCR** (Z.AI) | 03.02.2026, MIT | 0.9B | Да (`ru` в model card) | Да + JSON-extraction режим | 1.86 стр/с PDF; от 4 ГБ VRAM | **Официально Ollama** (`ollama run glm-ocr`), vLLM | [github](https://github.com/zai-org/GLM-OCR); [HF](https://huggingface.co/zai-org/GLM-OCR) |
| **MinerU2.5-Pro** | 2605-1.2B, 21.05.2026 | 1.2B | Мультиязычный [RU явно не подтверждён] | Да | ≈MinerU2.5 | vLLM, `mineru` 3.4.0 (18.06.2026) | [HF](https://huggingface.co/opendatalab/MinerU2.5-Pro-2605-1.2B); [arXiv 2604.04771](https://arxiv.org/abs/2604.04771) |
| **PaddleOCR-VL-1.6** | 28.05.2026 | 0.9B | Да (109 языков) | Да | Даже CPU; GGUF Q4 ~300 МБ | llama.cpp/GGUF, vLLM | [arXiv 2606.03264](https://arxiv.org/abs/2606.03264); [paddleocr.ai](https://www.paddleocr.ai/main/en/version3.x/algorithm/PaddleOCR-VL/PaddleOCR-VL-1.6.html) |
| **DeepSeek-OCR 2** | 27.01.2026 | 3B | [RU не подтверждён] | Да | 256–1120 виз. токенов (−80%) | vLLM, Transformers | [github](https://github.com/deepseek-ai/DeepSeek-OCR-2); [arXiv 2601.20552](https://arxiv.org/abs/2601.20552) |
| **Unlimited-OCR** (Baidu) | 22.06.2026, MIT | 3B MoE | [RU не подтверждён] | Да | 40+ страниц одним проходом | Transformers, vLLM | [github](https://github.com/baidu/Unlimited-OCR); [arXiv 2606.23050](https://arxiv.org/abs/2606.23050) |
| **Qwen3.6** 27B / 35B-A3B | 04.2026, Apache 2.0 | 27B (~17 ГБ Q4) | Да | Да | как qwen3-vl-30b | `ollama run qwen3.6:27b`, vLLM | [NVIDIA NIM docs](https://docs.nvidia.com/nim/vision-language-models/1.7.0/examples/qwen3.6/api.html) |
| **Qwen3.5** (0.8–397B) | 16.02.2026 | малые 0.8–9B | Да | Да | 9B в 24 ГБ | Ollama, llama.cpp | [qwen.ai blog](https://qwen.ai/blog?id=qwen3.5) |
| **HunyuanOCR** | 25.11.2025 | 1B | SOTA-14-языков [состав не проверен] | Да + координаты | лёгкая | vLLM (Ollama нет) | [arXiv 2511.19575](https://arxiv.org/abs/2511.19575) |
| **Chandra 2** (Datalab) | 03.2026 | 4B | Да (90+ языков) | Да, формы/чекбоксы, **сильная рукопись** | 2× быстрее v1 | pip chandra-ocr, vLLM | [github](https://github.com/datalab-to/chandra) |
| **Surya 2** (Datalab) | 2026 | 650M | 91 язык | Да | ~5 стр/с (RTX 5090) | pip, CPU/GPU | [datalab.to/blog/surya-2](https://www.datalab.to/blog/surya-2) |
| **dots.mocr** | 16.02.2026 | ~3B | «все письменности» | Да | — | HF, vLLM | [github](https://github.com/rednote-hilab/dots.ocr) |
| **olmOCR 2** (Ai2) | конец 2025 | 7B | **Нет — англ.** | Да | FP8 | vLLM | [allenai.org/blog/olmocr-2](https://allenai.org/blog/olmocr-2) |

**Бенчмарки (OmniDocBench):** v1.6 (30.04.2026): PaddleOCR-VL-1.6 — **96.33** (SOTA),
MinerU2.5-Pro — 95.69–95.75, GLM-OCR — 95.22. v1.5: GLM-OCR — 94.62 (#1), DeepSeek-OCR 2 —
91.09, Gemini-3.1-Pro — 90.33. Бенчмарк почти насыщен
([LlamaIndex, 24.02.2026](https://www.llamaindex.ai/blog/omnidocbench-is-saturated-what-s-next-for-ocr-benchmarks));
появился **Real5-OmniDocBench** для *фотографированных* документов
([arXiv 2603.04205](https://arxiv.org/html/2603.04205v1)) — открытые модели проседают
в среднем на 17.8%, особенно на не-латинице.

## Топ-3 кандидата для Botkin

1. **GLM-OCR — главный кандидат** (печатные лаб-результаты): русский официально;
   Information Extraction со строгой JSON-схемой ложится на пайплайн; 1.86 стр/с против
   ~26 с/док — потенциально на порядок быстрее; 0.9B в 4 ГБ VRAM — можно держать
   резидентно рядом с qwen3-vl. Нулевое трение: официальная модель в Ollama.
   Риск: кириллица на реальных RU-медбланках публично не бенчмаркалась — прогнать
   на корпусе 34 доков.
2. **Qwen3.6-27B (или Qwen3.5-9B)** — эволюционный апгрейд для сложных случаев
   (рукописные рецепты, фото): по русскоязычному сравнению 6 моделей
   ([Хабр, 16.11.2025](https://habr.com/ru/articles/966846/)) семейство Qwen3-VL —
   единственное, вменяемо читающее русскую рукопись. Рациональная схема — **каскад**:
   pymupdf → GLM-OCR (печатные сканы) → Qwen3.6 (рукопись/фото).
3. **MinerU2.5-Pro-1.2B** — эталон качества и «вторая линия» для перекрёстной
   проверки таблиц (двойное распознавание → сверка расхождений).

## Чего НЕ делать

- **olmOCR 2** — только английский.
- **Флагманы Qwen3.5-397B / облачный Qwen3.7-Plus** — не влезает в 24 ГБ / облако = утечка ПДн.
- **PaddleOCR-VL как основной для кириллицы без валидации** — зафиксированы замены
  кириллицы латиницей («МОСКВА» → «MOCKBA», [Хабр](https://habr.com/ru/articles/966846/)) —
  ровно проблема mixed-script (МСHС), с которой проект уже боролся.
- **Chandra 2** — лучший по рукописи, но лицензия OpenRAIL-M: коммерческий self-hosting
  требует платной лицензии.
- **Unlimited-OCR / dots.mocr** — сырые/не в профиль (40+ страниц не нужно для 1–2-страничных бланков).
- **Не менять модель ради +1 пункта бенчмарка**: OmniDocBench насыщен, у проекта уже 100%
  на своём корпусе. Реальный выигрыш — **скорость** (GLM-OCR) и **рукопись** (Qwen3.6).

## Следующий шаг

`ollama pull glm-ocr` → прогнать корпус из 34 документов, замерить точность и с/документ
против qwen3-vl; отдельно собрать мини-корпус рукописных рецептов и сравнить
qwen3-vl vs qwen3.6:27b.
