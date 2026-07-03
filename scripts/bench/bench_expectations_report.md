# Ожидания vs реальность: локальные OCR/VLM на русских медицинских документах

*Прогон: 2026-07-03 10:29 UTC. Железо: NVIDIA GeForce RTX 3080 Laptop GPU, 16384 MiB.*
*Корпус: 34 документов с эталонной разметкой (tests/fixtures/documents/samples/, sidecar `.expected.json`).*

**Как читать.** «Ожидание» — публичные бенчи из model card/лидербордов (источники — docs/ocr-models-research-2026-07.md, Часть 2). «Реальность» — точность извлечения эталонных значений и время на документ ЭТОГО прогона. Прямое численное сравнение колонок некорректно (OmniDocBench не содержит русского, скорость вендоры меряют на другом железе) — в этом и смысл таблицы.

| Модель | Ожидание: OmniDocBench | Ожидание: скорость | Реальность: точность | Реальность: PASS | Реальность: с/док | Вердикт |
|---|---|---|---|---|---|---|
| **qwen3-vl:8b-instruct** | н/д для 8B (235B-версия: 89.78 на v1.6) | н/д | **100.0%** (325/325) | 34/34 | 25.7 | ✅ соответствует |
| **glm-ocr** | 95.22 (v1.6, официальный лидерборд OpenDataLab) | 1.86 стр/с PDF (Z.ai; GPU не указан, конкурентность 1) | **81.8%** (248/303) | 28/34 | 13.7 | ❌ существенно ниже |
| **qwen2.5vl:7b** | v1.0: overall edit 0.226 EN / 0.324 ZH (tech report; шкала иная, не сравнивать с v1.5+) | н/д | **76.3%** (248/325) | 27/34 | 27.3 | ❌ существенно ниже |

## Паспорт ожиданий (первоисточники)

### qwen3-vl:8b-instruct
- Диск: 17.5 ГБ BF16 / ~5–6 ГБ Q4 (8.77B параметров)
- Русский: OCR на 32 языках заявлен (кириллица входит); olmOCR-bench 64.6±1.1 [независимый замер Datalab]
- Источники: https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct; https://huggingface.co/datalab-to/chandra-ocr-2; https://github.com/QwenLM/Qwen3-VL

### glm-ocr
- Диск: 2.65 ГБ BF16 (1.33B параметров суммарно)
- Русский: ru заявлен в model card (zh/en/fr/es/ru/de/ja/ko); бенчей по кириллице нет
- Источники: https://github.com/zai-org/GLM-OCR; https://github.com/opendatalab/OmniDocBench; https://docs.z.ai/guides/vlm/glm-ocr

### qwen2.5vl:7b
- Диск: 16.6 ГБ BF16 (8.29B параметров)
- Русский: мультиязычный OCR заявлен, ru без цифр
- Источники: https://arxiv.org/abs/2502.13923

## Детали прогона по документам

### qwen3-vl:8b-instruct
- провалов нет
- classify: 88s, extract: 786s, wall: 895s

### glm-ocr
- провалы: sample_001.pdf (2/3), sample_003.pdf (4/11), sample_012.pdf (44/47), sample_013.pdf (35/36), sample_016.pdf (22/63), sample_019.pdf (23/25)
- classify: 0s, extract: 466s, wall: 703s

### qwen2.5vl:7b
- провалы: sample_001.pdf (2/3), sample_003.pdf (6/11), sample_006.pdf (0/20), sample_011.pdf (4/20), sample_012.pdf (45/47), sample_016.pdf (32/63), sample_019.pdf (23/25)
- classify: 52s, extract: 877s, wall: 995s
