"""Промты для VLM/LLM-вызовов, вынесенные в markdown-ресурсы (llm/prompts/*.md).

Формат файла — блок метаданных (плоские "ключ: значение", без списков — полноценный
YAML не нужен для этих полей) в начале файла, отделённый строками "---", и тело файла —
собственно текст промта:

    ---
    version: 2026-07-25
    model_target: qwen3-vl:8b-instruct
    purpose: краткое назначение промта
    instruction: короткая user-инструкция (опционально)
    ---
    <текст промта>

`version` каждого файла независим — меняй его при ЛЮБОМ изменении текста этого промта.
Общий `PROMPTS_VERSION` (для лога вызовов classify/extract) — самая свежая версия среди
core-промтов основного VLM-пайплайна; иначе по логам не отличить регрессию промта от
регрессии модели после апгрейда Ollama.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Prompt:
    """Один промт-файл: метаданные + текст."""

    name: str
    version: str
    model_target: str
    purpose: str
    text: str
    instruction: str | None = None
    system: str | None = None


def _parse(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---\n"):
        raise ValueError("Промт-файл должен начинаться с блока метаданных '---'")
    _, header, body = raw.split("---\n", 2)
    meta: dict[str, str] = {}
    for line in header.splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, body.strip("\n")


@functools.lru_cache(maxsize=None)
def load_prompt(name: str) -> Prompt:
    """Читает llm/prompts/<name>.md, кэширует по имени файла (без расширения)."""
    raw = (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    meta, body = _parse(raw)
    return Prompt(
        name=name,
        version=meta.get("version", "unknown"),
        model_target=meta.get("model_target", ""),
        purpose=meta.get("purpose", ""),
        instruction=meta.get("instruction"),
        system=meta.get("system"),
        text=body,
    )


_classify = load_prompt("classify")
_analysis_vlm = load_prompt("analysis_vlm")
_doctor_report = load_prompt("doctor_report")
_analysis_text = load_prompt("analysis_text")
_analysis_text_compact = load_prompt("analysis_text_compact")
_image_ocr = load_prompt("image_ocr")
_sibr_ocr = load_prompt("sibr_ocr")
_rag_recommend = load_prompt("rag_recommend")
_lifestyle_recommend = load_prompt("lifestyle_recommend")

# Обратная совместимость: плоские константы (как раньше в прежнем llm/prompts.py).
CLASSIFY_VLM_SYSTEM = _classify.text
CLASSIFY_INSTRUCTION = _classify.instruction
ANALYSIS_VLM_SYSTEM = _analysis_vlm.text
ANALYSIS_INSTRUCTION = _analysis_vlm.instruction
DOCTOR_REPORT_VLM_SYSTEM = _doctor_report.text
DOCTOR_REPORT_INSTRUCTION = _doctor_report.instruction
ANALYSIS_TEXT_SYSTEM = _analysis_text.text
TEXT_INSTRUCTION = _analysis_text.instruction
ANALYSIS_TEXT_COMPACT_SYSTEM = _analysis_text_compact.text

# OCR-специализированные промты (короткий системный текст + основной текст как user-сообщение).
IMAGE_OCR_PROMPT = _image_ocr.text
IMAGE_OCR_SYSTEM = _image_ocr.system
SIBR_OCR_PROMPT = _sibr_ocr.text
SIBR_OCR_SYSTEM = _sibr_ocr.system

# RAG-рекомендации пациенту.
RAG_RECOMMEND_SYSTEM = _rag_recommend.text
# Комплексные lifestyle-рекомендации (uncensored-модель).
LIFESTYLE_RECOMMEND_SYSTEM = _lifestyle_recommend.text

# Версия для лога вызовов classify/extract (основной VLM-пайплайн: классификация +
# извлечение). Берём самую свежую дату среди core-промтов пайплайна.
PROMPTS_VERSION = max(
    _classify.version, _analysis_vlm.version, _doctor_report.version,
    _analysis_text.version, _analysis_text_compact.version,
)
