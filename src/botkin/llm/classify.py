"""Классификатор типа документа через VLM (дешёвый вызов на уменьшенной 1-й странице).

Fast-path: цифровые PDF с годным текстовым слоем классифицируются по ключевым словам
без VLM — экономит ~25 с на документ. VLM вызывается только когда текстовый слой
недоступен (скан, фото) или ключевые слова не дают однозначного ответа.
"""
import logging
import time
from pathlib import Path

import instructor
from pydantic import BaseModel

from botkin.config import (
    VLM_MODEL, CLASSIFY_TEMPERATURE, VLM_MAX_TOKENS, VLM_NUM_CTX, IMAGE_CLASSIFY_LONG_SIDE,
    VLM_STRUCTURED_OUTPUT,
)
from botkin.domain.models import ClassifyResult, DocType
from botkin.exceptions import ClassificationError
from botkin.llm.client import (
    get_client, build_extra_body, build_retrying, default_options, usage_of,
    model_name,
)
from botkin.llm.metrics import metrics_of
from botkin.llm.timing import timed
from botkin.llm.constants import (
    LAB_TABLE_MARKERS, UNKNOWN_TITLE_KEYWORDS, DOCTOR_REPORT_TITLE_KEYWORDS,
    ANALYSIS_TITLE_KEYWORDS, QUOTED_ORG_RE, has_keyword,
)
from botkin.llm.prompts import CLASSIFY_INSTRUCTION, CLASSIFY_VLM_SYSTEM, PROMPTS_VERSION
from botkin.preprocess.images import prepare_images, to_base64_jpegs
from botkin.preprocess.pdf_text import open_pdf

log = logging.getLogger(__name__)


def _correct_classification_by_content(doc_type: str, title: str | None, visible_text: str | None) -> str:
    """Корректирует VLM-классификацию по ключевым словам title и visible_text.

    Модель иногда путает рецепт с заключением врача и наоборот.
    Title и visible_text достаются из того же вызова, поэтому дешевле и надёжнее
    поправить очевидные случаи правилом, чем гнать дополнительный VLM-вызов.
    """
    title_lower = (title or "").lower()
    text_lower = (visible_text or "").lower()

    # Явный лабораторный заголовок — сильнейший сигнал, проверяем первым.
    if has_keyword(title_lower, ANALYSIS_TITLE_KEYWORDS):
        return "analysis"

    # Сначала смотрим на видимый текст — он конкретнее и реже галлюцинирует.
    if text_lower:
        if has_keyword(text_lower, UNKNOWN_TITLE_KEYWORDS):
            return "unknown"
        if has_keyword(text_lower, DOCTOR_REPORT_TITLE_KEYWORDS):
            return "doctor_report"

    # Fallback на title.
    if title_lower:
        if has_keyword(title_lower, UNKNOWN_TITLE_KEYWORDS):
            return "unknown"
        if has_keyword(title_lower, DOCTOR_REPORT_TITLE_KEYWORDS):
            return "doctor_report"

    return doc_type


def _detect_clinic(pdf) -> str | None:
    """Название организации из шапки текстового слоя (первая строка в кавычках) или None."""
    if not pdf.pages:
        return None
    header = " ".join(pdf.pages[0][:6])
    match = QUOTED_ORG_RE.search(header)
    return match.group(1).strip() if match else None


def _classify_from_text_layer(path: Path) -> ClassifyResult | None:
    """Fast-path для PDF: распознаёт лабораторный бланк по маркерам таблицы без VLM.

    Высокоточный, односторонний: уверенно подтверждает только `analysis` (бланк с колонками
    референса/единиц измерения, либо таблица «результат … значения»). Всё остальное —
    заключения, справки, сканы без слоя — возвращает None и уходит в VLM, который надёжно
    различает их. Так мы не рискуем ложно пометить заключение как анализ.
    """
    try:
        pdf = open_pdf(path)
    except Exception:
        return None
    if not pdf.is_usable:
        return None

    text_lower = pdf.flat_text.lower()
    is_lab = any(m in text_lower for m in LAB_TABLE_MARKERS) or (
        "результат" in text_lower and "значения" in text_lower
    )
    if not is_lab:
        return None

    clinic = _detect_clinic(pdf)
    log.info("[CLASSIFY_FAST] '%s' → analysis (текстовый слой, без VLM, clinic=%s)", path.name, clinic)
    return ClassifyResult(doc_type="analysis", confidence=0.98, clinic=clinic)


class ClassifySchema(BaseModel):
    doc_type: DocType
    confidence: float
    title: str | None = None
    clinic: str | None = None
    visible_text: str | None = None


def run_vlm(source_path: Path) -> ClassifyResult:
    """Классифицирует документ. PDF с годным текстовым слоем — без VLM; остальные — через VLM."""
    t0 = time.perf_counter()

    if source_path.suffix.lower() == ".pdf":
        fast = _classify_from_text_layer(source_path)
        if fast is not None:
            elapsed = time.perf_counter() - t0
            log.info(
                "[SUCCESS_CLASSIFY] Doc: '%s' | Result: '%s' (conf=%.2f) | fast-path | Elapsed: %.2fs",
                source_path.name, fast.doc_type, fast.confidence, elapsed,
            )
            return fast

    log.info("[START_CLASSIFY] Doc: '%s' | Model: %s", source_path.name, VLM_MODEL)

    images = prepare_images(source_path, long_side=IMAGE_CLASSIFY_LONG_SIDE)
    b64 = to_base64_jpegs(images[:1])   # только первая страница
    client = get_client(mode=instructor.Mode.JSON)

    content = [
        {"type": "text", "text": CLASSIFY_INSTRUCTION},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64[0]}"}},
    ]
    messages = [
        {"role": "system", "content": CLASSIFY_VLM_SYSTEM},
        {"role": "user", "content": content},
    ]

    t0_outer = time.perf_counter()
    try:
        with timed("CLASSIFY", source_path.name) as t:
            t0_call = time.perf_counter()
            response = client.chat.completions.create(
                model=model_name(VLM_MODEL),
                messages=messages,
                response_model=ClassifySchema,
                max_retries=build_retrying(),
                max_tokens=VLM_MAX_TOKENS,
                # CLASSIFY_TEMPERATURE (≈0.1): на дефолтной температуре Ollama растровые
                # бланки (Тонус, sample_011) флуктуируют между analysis/doctor_report.
                extra_body=build_extra_body(
                    ClassifySchema,
                    options={**default_options(), "temperature": CLASSIFY_TEMPERATURE},
                ),
            )
            elapsed_call = time.perf_counter() - t0_call
            t["metrics"] = metrics_of(response, model_name(VLM_MODEL), elapsed_call, num_ctx=VLM_NUM_CTX)

        elapsed = t["elapsed"]
        prompt_tokens, completion_tokens = usage_of(response)
        log.info(
            "[SUCCESS_CLASSIFY] Doc: '%s' | Result: '%s' (conf=%.2f) | Промпты: %s | Схема: %s | "
            "Elapsed: %.2fs | Prompt: %d t | Completion: %d t",
            source_path.name, response.doc_type, response.confidence, PROMPTS_VERSION,
            "on" if VLM_STRUCTURED_OUTPUT else "off",
            elapsed, prompt_tokens, completion_tokens,
        )
        corrected_type = _correct_classification_by_content(response.doc_type, response.title, response.visible_text)
        return ClassifyResult(
            doc_type=corrected_type, confidence=response.confidence,
            title=response.title, clinic=response.clinic,
        )
    except Exception as e:
        elapsed = time.perf_counter() - t0_outer
        log.error("[FAILED_CLASSIFY] Doc: '%s' | Elapsed: %.2fs | Error: %s", source_path.name, elapsed, e)
        raise ClassificationError(f"Сбой классификации: {e}") from e
