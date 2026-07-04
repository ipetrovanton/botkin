# Ожидания vs реальность: локальные OCR/VLM на русских медицинских документах

*Прогон: 2026-07-04 14:47 UTC. Железо: NVIDIA GeForce RTX 3080 Laptop GPU, 16384 MiB.*
*Корпус: 34 документов с эталонной разметкой (tests/fixtures/documents/samples/, sidecar `.expected.json`).*

**Как читать.** «Ожидание» — публичные бенчи из model card/лидербордов (источники — docs/ocr-models-research-2026-07.md, Часть 2). «Реальность» — точность извлечения эталонных значений и время на документ ЭТОГО прогона. Прямое численное сравнение колонок некорректно (OmniDocBench не содержит русского, скорость вендоры меряют на другом железе) — в этом и смысл таблицы.

| Модель | Ожидание: OmniDocBench | Ожидание: скорость | Реальность: точность | Реальность: PASS | Реальность: с/док | Вердикт |
|---|---|---|---|---|---|---|
| **qwen3-vl:8b-instruct** | н/д для 8B (235B-версия: 89.78 на v1.6) | н/д | **100.0%** (325/325) | 34/34 | 25.4 | ✅ соответствует |
| **qwen3.5:9b** | 87.7 (v1.5, self-reported; превосходит qwen3-vl-30b: 86.8) | н/д; ~40–80 tok/s на 16GB GPU (oamazonasgabriel/qwen3.5-9b) | **74.1%** (240/324) | 26/34 | 125.4 | ❌ существенно ниже |
| **haervwe/GLM-4.6V-Flash-9B** | н/д (модель не входила в ресёрч — дополнить docs/ocr-models-research) | н/д | **98.5%** (320/325) | 31/34 | 98.0 | ⚠️ ниже базовой |

## Паспорт ожиданий (первоисточники)

### qwen3-vl:8b-instruct
- Диск: 17.5 ГБ BF16 / ~5–6 ГБ Q4 (8.77B параметров)
- Русский: OCR на 32 языках заявлен (кириллица входит); olmOCR-bench 64.6±1.1 [независимый замер Datalab]
- Источники: https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct; https://huggingface.co/datalab-to/chandra-ocr-2; https://github.com/QwenLM/Qwen3-VL

### qwen3.5:9b
- Диск: 19.3 ГБ BF16 / ~6.6 ГБ Q4_K_M (9.65B параметров, dense)
- Русский: 201 язык (текст); OCR-языки — н/д, но кириллица в составе мультиязычного OCR
- Источники: https://huggingface.co/Qwen/Qwen3.5-9B; https://ollama.com/library/qwen3.5:9b; https://github.com/QwenLM/Qwen3.5

### haervwe/GLM-4.6V-Flash-9B
- Диск: н/д
- Русский: н/д
- Источники: —

## Детали прогона по документам

### qwen3-vl:8b-instruct
- провалов нет
- classify: 84s, extract: 779s, wall: 881s

### qwen3.5:9b
- провалы: sample_001.pdf (2/3), sample_003.pdf (6/11), sample_006.pdf (0/20), sample_011.pdf (0/20), sample_012.pdf (44/47), sample_013.pdf (35/36), sample_016.pdf (31/63), sample_019.pdf (23/25)
- classify: 286s, extract: 3978s, wall: 5107s

### haervwe/GLM-4.6V-Flash-9B
- провалы: sample_011.pdf (19/20), sample_012.pdf (45/47), sample_019.pdf (23/25)
- classify: 252s, extract: 3081s, wall: 3412s
