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

---

# Часть 2 (03.07.2026): бенчмарки локальных моделей — «ожидания» для будущей сверки с реальностью

*Все числа — из первоисточников (model cards HF, GitHub README, arXiv, официальные блоги),
проверены 03.07.2026. Размеры весов и лицензии — из HuggingFace API (точные байты
`usedStorage` / параметры safetensors). Числа из вторичных обзоров помечены
**[вторичный источник]**. Раздел собран для статьи на Хабр: после локального прогона
на корпусе русских меддокументов сюда добавится колонка «реальность».*

## Контекст по версиям OmniDocBench

v1.5 (25.09.2025, 1355 стр.), v1.6 (10.04.2026: +296 сложных страниц, новый матчинг MGAM),
v1.7 (30.04.2026, добавлен только Qianfan-OCR). Официальный лидерборд v1.6 — в README
[github.com/opendatalab/OmniDocBench](https://github.com/opendatalab/OmniDocBench).
Overall = ((1−TextEdit)×100 + TableTEDS + FormulaCDM)/3.
**Языки бенча: только en / simplified_chinese / en-zh mixed — русского в OmniDocBench НЕТ.**

## Сводная таблица

| Модель | Лицензия | Диск (формат) | Мин. VRAM | OmniDocBench (версия!) | Другие бенчи | Скорость (GPU) | Русский язык | Источники |
|---|---|---|---|---|---|---|---|---|
| **GLM-OCR** (zai-org, 0.9B декодер, всего 1.33B) | Веса: MIT (HF card); код: Apache-2.0 | 2.65 ГБ (safetensors BF16) | н/д от вендора; ~4 ГБ оценочно (есть Ollama/MLX) | **94.62 на v1.5** (№1 на момент релиза); **95.22 на v1.6** (официальный лидерборд: TextEdit 0.044, TableTEDS 92.83, CDM 97.18) | внутренние оценки Z.ai: код, печати, счета | **1.86 стр/с PDF, 0.67 изобр/с** — Z.ai, конкурентность 1, *GPU не указан* | заявлена поддержка ru (Z.ai docs: zh/en/fr/es/ru/de/ja/ko); бенчей по кириллице нет | [1][2][3][4] |
| **PaddleOCR-VL-1.6** (Baidu, 0.9B) | Apache-2.0 | 1.93 ГБ (BF16); INT8 ~1 ГБ [вторичный: Spheron] | ~2–4 ГБ | **96.33 на v1.6 (self-reported, 03.06.2026)** — SOTA; в официальном лидерборде v1.6 пока только v1.5 = 94.93; SOTA на Real5-OmniDocBench (бенч самого Baidu) | olmOCR-bench: 80.0 для v1.0 [замер dots.mocr] | н/д для 1.6 | 109 языков заявлено (arXiv 2510.14528); Хабр-тест: «МОСКВА»→«MOCKBA» | [5][6][7][22] |
| **MinerU2.5-Pro-2605-1.2B** (OpenDataLab) | Apache-2.0 | ~2.3 ГБ BF16 (1.156B параметров) | ~4 ГБ оценочно | **95.75 на v1.6 (официальный лидерборд — №1 среди опубликованных)**; TextEdit 0.036, CDM 97.45, TEDS 93.42 | 5 табличных бенчей — №1 (свой замер) | **2.12 fps на 1×A100** (vllm-async, конкурентно) | «native multilingual OCR» в релизе 2605; ru явно — н/д | [8][9][2] |
| **DeepSeek-OCR-2** (3.39B) | Apache-2.0 | 6.78 ГБ BF16 | ~8–10 ГБ оценочно | **91.09 на v1.5** (self-reported); **90.25 на v1.6** (официальный лидерборд) | экономия визуальных токенов: ≈ до 1120 ток./стр. | н/д для v2 | н/д (у v1 ~100 языков, v2 карточка молчит) | [10][11][2] |
| **Qwen3-VL-8B-Instruct** (8.77B) | Apache-2.0 | 17.5 ГБ BF16 (Q4 ~5–6 ГБ) | 24 ГБ BF16 / ~8 ГБ Q4 | 8B не мерили (235B: 89.78 на v1.6) | **olmOCR-bench: 64.6±1.1** [независимый замер Datalab] — ниже спец-моделей | н/д | OCR на 32 языках заявлен, кириллица входит | [12][13][14] |
| **Qwen2.5-VL-7B** (8.29B, база сравнения) | Apache-2.0 | 16.6 ГБ BF16 | 24 ГБ / ~6–8 ГБ Q4 | v1.0: overall edit 0.226 EN / 0.324 ZH (tech report) | DocVQA 96.4 (свой отчёт) | н/д | мультиязычный OCR заявлен, ru без цифр | [15] |
| **Qwen3.5-9B** (9.65B, 02.03.2026) | Apache-2.0 | 19.3 ГБ BF16 | 24 ГБ / ~7 ГБ Q4 | **87.7 на v1.5** (self-reported) | **OCRBench: 89.2** (self-reported) | н/д; MTP-спекулятивный декодинг | 201 язык (текст); OCR-языки — н/д | [16] |
| **Qwen3.6-27B** (27.8B, 22.04.2026) | Apache-2.0 | 55.6 ГБ BF16; **в 24 ГБ только Q4 GGUF (~15–17 ГБ)** | ~20 ГБ Q4 | н/д | **OCRBench: 89.4**, CC-OCR: 81.2 (self-reported) | н/д | как Qwen3.5 — н/д | [17] |
| **HunyuanOCR** (Tencent, 1B) | **«other»: не действует в ЕС/UK/Ю.Корее** | 1.99 ГБ BF16 | ~3–4 ГБ | **94.10 self-reported** → **89.95 на официальном v1.6** (−4.15 — показательный разрыв!) | OCRBench 860 (SOTA <3B на релиз, self-reported) | н/д | ~130 языков заявлено | [18][19][2] |
| **Surya 2** (Datalab, 686M) | Код Apache-2.0; **веса OpenRAIL-M** (бесплатно: research/personal/стартапы <$5M) | ~1.4 ГБ BF16 + GGUF | ~2 ГБ; CPU/Apple Silicon | не участвует | **olmOCR-bench: 83.3%** (топ среди <3B, self-reported); 87.2% на внутреннем 91-языковом | **5.35 стр/с на RTX 5090** при 128 конкурентных | ru в 91 языке внутреннего бенча (разбивки нет) | [20][21] |
| **Chandra 2** (Datalab; «4B» = 5.30B на HF) | Код Apache-2.0; **веса OpenRAIL-M** (<$2M; нельзя конкурировать с их API) | 10.6 ГБ BF16 | ~16 ГБ оценочно | не участвует | **olmOCR-bench: 85.8±0.8 (SOTA open, 18.03.2026)**; 43-языковый бенч: 77.8% | **1.44 стр/с на 1×H100** (96 конкурентных) | **ru: 85.5%** (внутр. бенч; у Chandra 1 было 88.7% — v2 на русском просела!) | [22][23] |
| **dots.mocr** (rednote-hilab, 3.04B) | MIT | 6.08 ГБ BF16 | ~8 ГБ оценочно | v1.5 self-reported: TextEdit 0.031, ReadOrder 0.029 — лучшие; overall не публикуют | **olmOCR-bench: 83.9±0.9**; сильная сторона — графики/схемы → SVG | н/д | мультиязычный; поязычных цифр по ru нет | [24][25] |
| **Nanonets-OCR-s** (3.75B) | не указана в карточке | 7.52 ГБ BF16 | ~8 ГБ | не участвует | заморожен (посл. обновление 20.06.2025); преемник OCR2-3B: olmOCR-bench 69.5 [замер dots.mocr] | н/д | англоцентричная | [26][25] |
| **GOT-OCR2** (stepfun-ai, 716M) | Apache-2.0 | 1.43 ГБ BF16 | ~2–3 ГБ | отсутствует в v1.5/v1.6 (модель 09.2024) | формулы CDM 74.1 (модульная таблица) | н/д | **нет**: en/zh, кириллица не заявлена | [2][27] |

*«Мин. VRAM ~оценочно» — вендоры почти не публикуют минимум; оценка = веса BF16 +
KV-cache/активации. В статью выносить как расчёт, а не как факт.*

### Источники к таблице

1. https://github.com/zai-org/GLM-OCR
2. https://github.com/opendatalab/OmniDocBench (лидерборд v1.6_full)
3. https://docs.z.ai/guides/vlm/glm-ocr
4. https://huggingface.co/zai-org/GLM-OCR + arXiv:2603.10910
5. https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6
6. https://arxiv.org/abs/2606.03264
7. https://arxiv.org/abs/2510.14528
8. https://huggingface.co/opendatalab/MinerU2.5-Pro-2605-1.2B (arXiv:2604.04771)
9. https://github.com/opendatalab/MinerU
10. https://huggingface.co/deepseek-ai/DeepSeek-OCR-2 (arXiv:2601.20552)
11. https://comfyui-wiki.com/en/news/2026-01-27-deepseek-ocr-2-release [вторичный]
12. https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct
13. https://huggingface.co/datalab-to/chandra-ocr-2 (таблица olmOCR-bench)
14. https://github.com/QwenLM/Qwen3-VL
15. arXiv:2502.13923 (Qwen2.5-VL tech report)
16. https://huggingface.co/Qwen/Qwen3.5-9B
17. https://huggingface.co/Qwen/Qwen3.6-27B + https://github.com/QwenLM/Qwen3.6
18. https://arxiv.org/abs/2511.19575 (HunyuanOCR tech report)
19. https://huggingface.co/tencent/HunyuanOCR (текст лицензии)
20. https://github.com/datalab-to/surya
21. https://www.datalab.to/blog/surya-2
22. https://huggingface.co/datalab-to/chandra-ocr-2
23. https://www.datalab.to/blog/chandra-2
24. https://huggingface.co/rednote-hilab/dots.mocr (arXiv:2603.13032)
25. там же — таблица olmOCR-bench
26. https://huggingface.co/nanonets/Nanonets-OCR-s и /Nanonets-OCR2-3B
27. https://huggingface.co/stepfun-ai/GOT-OCR2_0

## Независимые исследования 2025–2026

1. **«OmniDocBench is Saturated, What's Next for OCR Benchmarks?»** — Jerry Liu (LlamaIndex),
   24.02.2026, https://www.llamaindex.ai/blog/omnidocbench-is-saturated-what-s-next-for-ocr-benchmarks.
   Методика: разбор метрик на конкретных фейлах LlamaParse. Вывод: бенч насыщен (>94% у топов)
   и штрафует семантически корректные, но иначе отформатированные ответы.
2. **«Современные OCR для сложных документов: сравниваем 6 open-source моделей»** — Хабр,
   aak204, 16.11.2025, https://habr.com/ru/articles/966846/. Методика: 3 русскоязычных
   документа (скан+рукопись, печать, рукопись), 6 моделей, единый промпт. Выводы: на печати
   все норм, PaddleOCR-VL подменяет кириллицу латиницей; русскую рукопись вытянули только
   Qwen3-VL/Omni.
3. **«Как мы оценивали OCR на русских документах»** — Хабр, блог «Честного знака» (43Tech),
   24.04.2026, https://habr.com/ru/companies/chestnyznak/articles/1027484/. Методика:
   6 датасетов из реальных DOCX → PDF без текстового слоя, метрики Левенштейна + структурные
   + производительность. Вывод: на русских документах со сложной геометрией композиция
   специализированных лёгких компонентов обошла универсальные VLM; VLM выигрывают только
   в пакетной параллельной обработке.
4. **OCR Arena** — краудсорсинговый Elo-лидерборд «вслепую», https://www.ocrarena.ai/.
   Вывод (ссылка rednote-hilab, март 2026): порядок «Gemini 3 Pro > dots.mocr > HunyuanOCR >
   PaddleOCR-VL/GLM-OCR» на реальных документах не совпадает с порядком OmniDocBench.

Бонус (VRAM/throughput): «Best Open-Source OCR and Document VLMs to Self-Host» — Spheron, 2026,
https://www.spheron.network/blog/best-open-source-ocr-vlm-self-host-gpu-cloud-2026/ [вторичный, вендор облака].

## Каверзные места при сравнении бенчей (для честного «ожидания vs реальность»)

1. **Версии OmniDocBench несравнимы.** v1.5→v1.6 сменил датасет (+296 страниц) и матчинг
   (MGAM). GLM-OCR: 94.62 (v1.5) и 95.22 (v1.6). Смешивать колонки разных версий —
   главный способ соврать таблицей.
2. **Self-reported vs официальная переоценка.** HunyuanOCR: 94.1 в своём tech report →
   89.95 в официальном v1.6 (−4.15). PaddleOCR-VL-1.6 (96.33) в официальном лидерборде
   пока отсутствует — SOTA только self-reported.
3. **Скорость меряют на разном железе и конкурентности.** GLM-OCR 1.86 стр/с (GPU не назван,
   b=1), MinerU2.5-Pro 2.12 fps (A100, async), Chandra 2 1.44 стр/с (H100, 96 параллельных),
   Surya 5.35 стр/с (RTX 5090, 128 параллельных). На бытовой карте с batch=1 всё упадёт в разы.
4. **Насыщение метрик.** При 94–96% overall различия меньше шума разметки; edit distance
   штрафует безобидные различия (HTML vs Markdown таблиц).
5. **Скан vs фото.** OmniDocBench — «чистые» рендеры/сканы; фото с перекосом — отдельный
   Real5-OmniDocBench (сделан самим Baidu — учитывать конфликт интересов).
6. **Русского нет ни в одном крупном публичном OCR-бенче.** Кириллица — только во внутренних
   бенчах Datalab (Chandra 2: ru 85.5%, хуже Chandra 1 — 88.7%!) и в claims карточек.
   Ожидания по OmniDocBench на русские меддокументы не переносятся — главный тезис
   раздела «ожидания vs реальность».
7. **«Размер модели» — маркетинг.** Chandra 2 «4B» = 5.30B на HF (10.6 ГБ); GLM-OCR «0.9B» =
   1.33B суммарно. Считать по safetensors, не по названию.
8. **Лицензии весов ≠ лицензии кода.** Datalab: код Apache-2.0, веса OpenRAIL-M с порогом
   выручки; HunyuanOCR не лицензируется в ЕС/UK/Корее; GLM-OCR: GitHub Apache-2.0 vs HF MIT.
9. **Elo-оценки судятся LLM** (судья Gemini-3-Flash у dots.mocr) — у LLM-judge свои смещения.

---

# Часть 3 (03.07.2026): Реальность — замер на корпусе русских меддокументов

*Замер: 2026-07-03. Железо: NVIDIA GeForce RTX 3080 Laptop GPU, 16384 MiB VRAM,
driver 610.47. Ollama 0.31.1. Корпус: 34 документа (20 PDF + 14 JPG) с эталонной
разметкой в `tests/fixtures/documents/samples/` (sidecar `.expected.json`).
Методика: `scripts/bench/bench_expectations.py --pull` — e2e-пайплайн
(classify → extract → match) для каждой модели, метрика — доля совпавших
эталонных значений и время на документ. Логи: `bench_<model>.log` в корне репо.*

## Итоговая таблица «ожидание vs реальность»

| Модель | OmniDocBench (ожидание) | Скорость (ожидание) | Точность (реальность) | PASS (реальность) | с/док (реальность) | Вердикт |
|---|---|---|---|---|---|---|
| **qwen3-vl:8b-instruct** | н/д для 8B (235B: 89.78 v1.6) | н/д | **100.0%** (325/325) | 34/34 | 25.7 | ✅ соответствует |
| **glm-ocr** | 95.22 (v1.6, лидерборд) | 1.86 стр/с (GPU не указан) | **81.8%** (248/303) | 28/34 | 13.7 | ❌ существенно ниже |
| **qwen2.5vl:7b** | v1.0: edit 0.226 EN / 0.324 ZH | н/д | **76.3%** (248/325) | 27/34 | 27.3 | ❌ существенно ниже |

**Главный вывод:** ни одна из альтернатив не достигла точности qwen3-vl:8b-instruct
на русском медицинском корпусе. GLM-OCR, занимающий 1-е место на OmniDocBench v1.6
(95.22), на реальных русских документах потерял 18 процентных пунктов (81.8%).
Qwen2.5-VL-7B — 24 п.п. (76.3%). При этом OmniDocBench не содержит кириллицы, и
разрыв подтверждает тезис Части 2: **оценки на OmniDocBench не переносятся на
русские меддокументы**.

## Скорость: ожидание vs реальность

| Модель | Заявленная скорость | Реальная скорость | Расхождение |
|---|---|---|---|
| qwen3-vl:8b-instruct | н/д | 25.7 с/док (14.6 мин на корпус) | — |
| glm-ocr | 1.86 стр/с (Z.ai, GPU не указан) | 13.7 с/док (7.8 мин на корпус) | Прямое сравнение некорректно (разное железо, 1 стр/с ≠ 1 док/с), но 13.7 с/док при 1–2 стр. на бланк — правдоподобно |
| qwen2.5vl:7b | н/д | 27.3 с/док (15.5 мин на корпус) | — |

**Особенность glm-ocr:** classify=0.0с для всех документов — модель не вызывает
VLM-классификацию (используется fast-path по текстовому слою PDF). Для JPG-фото
заключений врача extract=0.0с — модель не пытается извлекать показатели из
изображений. Это объясняет часть ускорения: 15 JPG обработаны за 0 секунд.

## Топ-3 показательных провала

1. **sample_016.pdf × glm-ocr: 22/63 (35%).** Большой гематологический бланк
   (63 значения: ОАК + лейкоцитарная формула). glm-ocr извлёк только 23 строки,
   41 значение не найдено, включая «Нейтрофилы (общ. число), %: 56.0 %»,
   «Миелоциты: 0.0 %». qwen3-vl: 63/63 за 69.8с. Модель не справилась с
   плотной таблицей на 2 страницы — потеряла всю вторую страницу.

2. **sample_006.pdf × qwen2.5vl:7b: 0/20 (0%).** ПЦР-исследование
   (Андрофлор-подобная панель: «Геномная ДНК человека: 5.7 Lg», «Общая
   бактериальная масса: 4.8 Lg», «Lactobacillus spp.: 4.7 Lg»). qwen2.5vl
   извлёк 4 строки, ни одна не совпала с эталоном. qwen3-vl: 20/20 за 76.3с.
   Полный провал structured output — модель не поняла формат логарифмических
   значений (Lg).

3. **sample_003.pdf × обе модели: glm-ocr 4/11 (36%), qwen2.5vl 6/11 (55%).**
   Паразитологическая панель (антитела: «Лямблии, суммарные антитела классов
   IgM, IgA, IgG: 0.58 КП», «Эхинококк однокамерный, IgG: 0.18 КП»). Обе
   альтернативы потеряли 5+ значений из 11. qwen3-vl: 11/11 за 27.2с.
   Длинные названия показателей с вложенными квалификаторами (классы IgM/IgA/IgG)
   — системная трудность для моделей, обученных на коротких англоязычных лейблах.

## Кандидат на смену модели?

**Нет.** Условие промпта: «glm-ocr при точности ≥ qwen3-vl и большей скорости —
кандидат на смену». glm-ocr быстрее (13.7 vs 25.7 с/док), но точность 81.8%
против 100% — не проходит порог. GLM-OCR остаётся кандидатом на **каскадное
использование** (печатные сканы с простыми таблицами → glm-ocr, сложные случаи →
qwen3-vl), но не на замену.

Сырые данные: `scripts/bench/bench_expectations_results.json`.
Отчёт-таблица: `scripts/bench/bench_expectations_report.md`.

---

# Часть 4 (04.07.2026): Новое поколение — qwen3.5:9b и GLM-4.6V-Flash-9B

*Замер: 2026-07-04. Железо: NVIDIA GeForce RTX 3080 Laptop GPU, 16384 MiB VRAM,
driver 610.47. Ollama 0.31.1. Тот же корпус: 34 документа (20 PDF + 14 JPG).*

## Контекст

После прогона 3 моделей (Часть 3) проверены 2 новые модели, вышедшие в 2026:
- **qwen3.5:9b** (Alibaba, 02.03.2026) — нативная мультимодальная (text+image+video
  из одних весов), 9.65B dense. OmniDocBench v1.5: **87.7** — превосходит
  qwen3-vl-30b (86.8) при втрое меньшем размере.
- **GLM-4.6V-Flash-9B** (Zhipu AI, 2026) — vision-language из серии GLM-V
  (преемник GLM-4V-9B), 9B, Flash-вариант (облегченный).

## Итоговая таблица (все 5 моделей)

| Модель | OmniDocBench | Точность | PASS | с/док | Вердикт |
|---|---|---|---|---|---|
| qwen3-vl:8b-instruct | н/д для 8B | **100.0%** (325/325) | 34/34 | 25.4 | ✅ эталон |
| **GLM-4.6V-Flash-9B** | н/д | **98.5%** (320/325) | 31/34 | 98.0 | ⚠️ 1.5% от эталона |
| glm-ocr | 95.22 (v1.6) | 81.8% (248/303) | 28/34 | 13.7 | ❌ |
| qwen2.5vl:7b | v1.0: edit 0.226 EN | 76.3% (248/325) | 27/34 | 27.3 | ❌ |
| **qwen3.5:9b** | 87.7 (v1.5) | **74.1%** (240/324) | 26/34 | 125.4 | ❌ катастрофа |

## Главные находки

### 1. GLM-4.6V-Flash-9B — лучший кандидат после qwen3-vl

**98.5% точности** — всего 1.5 процентных пункта от qwen3-vl:8b-instruct (100%).
3 провала — все «почти PASS» (потеря 1–2 значений из 20–47):

- sample_011.pdf: 19/20 (потеря 1 значения из 20)
- sample_012.pdf: 45/47 (потеря 2 значений из 47 — вычисляемые показатели
  «Холестерин-ЛПНП по Фридвальду» и «Холестерин не-ЛПВП», модель правильно
  не выдумывает их)
- sample_019.pdf: 23/25 (потеря 2 значений из 25)

Все 14 JPG-фото заключений врача — PASS (модель видит изображения, в отличие
от glm-ocr). Все 17 PDF с таблицами — PASS кроме 3 «почти PASS».

**Скорость: 98.0 с/док** — в 3.9 раза медленнее qwen3-vl (25.4), но в 1.3 раза
быстрее qwen3.5:9b (125.4). Медлительность объясняется размером модели (8 ГБ
Q4 vs 6.1 ГБ) и отсутствием fast-path по текстовому слою (classify занимает
в среднем 7.4с на PDF с текстовым слоем, extract — 164с).

### 2. qwen3.5:9b — публичные бенчи не переносятся на vision/OCR через Ollama

**74.1% — худший результат** среди всех тестированных моделей, включая
qwen2.5vl:7b (76.3%). При том, что на OmniDocBench v1.5 qwen3.5:9b набирает
87.7, превосходя qwen3-vl-30b (86.8). Разрыв **13.6 п.п.** между бенчем и
реальностью.

**Причина: thinking mode.** qwen3.5:9b имеет thinking mode включённый по
умолчанию (Qwen docs: «Qwen3.5 enabled by default»). Для vision/OCR задач через
Ollama весь вывод уходит в `thinking` field, `content` остаётся пустым
([ollama/ollama#14502](https://github.com/ollama/ollama/issues/14502)):
> «all output goes into the thinking field and content is always empty when
> images are involved. Tried everything: 'thinking': False in options — ignored
> for image inputs, /no_think in prompt — treated as literal text by the model»

**125.4 с/док** — в 5 раз медленнее qwen3-vl. Extract синтетического бланка:
98.9с (vs ~15с у qwen3-vl) — модель генерирует reasoning tokens, тратя бюджет
на «обдумывание» вместо извлечения.

### 3. Оптимизация: отключение thinking mode не спасает qwen3.5:9b

Попробована оптимизация через `chat_template_kwargs: {"enable_thinking": False}`
в extra_body (через env `VLM_DISABLE_THINKING=1`). Результат на 5 документах:
- 3 FAIL из 5 (sample_001, sample_003, sample_005, sample_006)
- Скорость ~3 мин/док (быстрее baseline 3.5 мин, но всё ещё в 7 раз медленнее
  qwen3-vl)

Отключение thinking mode улучшило скорость, но не точность. Модель теряет
значения даже без thinking — проблема в vision understanding, а не только в
thinking mode. **Вывод: qwen3.5:9b через Ollama не пригодна для vision/OCR
задач** — проблема на уровне интеграции Ollama ↔ Qwen3.5 vision pipeline.

### 4. Оптимизация: structured output (XGrammar) — двойственный эффект для GLM-4.6V

Прогон GLM-4.6V-Flash-9B без structured output (`VLM_STRUCTURED_OUTPUT=0`) на
3 проблемных документах:

| Документ | С XGrammar | Без XGrammar | Эффект |
|---|---|---|---|
| sample_011 | 19/20 (FAIL) | 0/20 (FAIL) | XGrammar критически важен |
| sample_012 | 45/47 (FAIL) | 44/47 (FAIL) | Примерно то же |
| sample_019 | 23/25 (FAIL) | **25/25 (PASS)** | XGrammar терял 2 значения! |

**Скорость без XGrammar: 751.5 с/док** (vs 98.0 с/док) — в 7.7 раз медленнее.
Модель генерирует свободный текст, Instructor парсит — катастрофа по времени.

**Вывод:** XGrammar имеет двойственный эффект — критически важен для большинства
документов (sample_011: 0→19) и для скорости (7.7×), но на отдельных документах
теряет 1–2 значения (sample_019: 23→25). Отключение непрактично из-за скорости.
Возможное решение: **адаптивный фолбэк** — при FAIL на 1–2 значения повторить
без XGrammar (уже реализовано в extract.py для пустых ответов, но не для
«почти полных»).

## Обновлённый топ кандидатов для Botkin

1. **qwen3-vl:8b-instruct** — остаётся боевой моделью. 100%, 25.4 с/док.
2. **GLM-4.6V-Flash-9B** — **новый кандидат №2** (раньше был glm-ocr). 98.5%,
   98.0 с/док. Сильнее на сложных таблицах (sample_016: 63/63 vs glm-ocr 22/63)
   и на JPG-фото (14/14 PASS vs glm-ocr 14/14 PASS, но с extract=0 у glm-ocr).
   Медленнее qwen3-vl в 4 раза, но точность сопоставима.
3. **glm-ocr** — кандидат на каскад (печатные PDF с простыми таблицами, 2×
   быстрее qwen3-vl). Не работает с JPG.
4. **qwen3.5:9b** — **не пригодна** для vision/OCR через Ollama. Thinking mode
   ломает vision pipeline. Следить за исправлением в Ollama
   ([#14502](https://github.com/ollama/ollama/issues/14502)).
5. **qwen2.5vl:7b** — исключена из покрытия. 76.3%, хуже и медленнее qwen3-vl.

## Источники (новые)

- qwen3.5:9b: https://huggingface.co/Qwen/Qwen3.5-9B (benchmarks),
  https://ollama.com/library/qwen3.5:9b, https://github.com/QwenLM/Qwen3.5
- GLM-4.6V-Flash-9B: https://github.com/zai-org/GLM-V,
  https://ollama.com/haervwe/GLM-4.6V-Flash-9B
- Thinking mode issue: https://github.com/ollama/ollama/issues/14502,
  https://github.com/ollama/ollama/issues/14793
- Qwen thinking docs: https://docs.qwencloud.com/developer-guides/text-generation/thinking
