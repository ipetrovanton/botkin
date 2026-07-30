"""Извлечение из текстового слоя PDF: compact-формат и JSON-схема.

Два пути:
1. Compact (основной): «имя|значение|единица|референс» — без JSON-схемы, в ~2 раза быстрее.
2. JSON-схема (фолбэк): если compact вернул 0 строк — structured output через instructor.

Также: _extract_from_text_layer — постраничная обработка PDF с текстовым слоем,
включая verbatim-страж, completeness_guard и растровые страницы (СИБР/Андрофлор).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Callable

import instructor
from pydantic import BaseModel

from botkin.config import (
    IMAGE_EXTRACT_LONG_SIDE, VERBATIM_MAX_REJECT_RATIO,
    OLLAMA_KEEP_ALIVE, RAW_LOG_LIMIT, TEXT_LAYER_TEMPERATURE,
    TEXT_MODEL, TEXT_MAX_TOKENS, TEXT_NUM_CTX, TEXT_NUM_PREDICT,
    TEXT_REPEAT_PENALTY, TEXT_STRUCTURED_OUTPUT, TEXT_COMPACT_OUTPUT,
)
from botkin.domain.models import LabResult
from botkin.exceptions import ExtractionError
from botkin.llm.client import (
    get_client, get_raw_client, build_extra_body, build_retrying, model_name,
)
from botkin.llm.metrics import metrics_of
from botkin.llm.prompts import (
    ANALYSIS_TEXT_SYSTEM, ANALYSIS_TEXT_COMPACT_SYSTEM, TEXT_INSTRUCTION,
)
from botkin.llm.salvage import salvage_rows, rows_or_harvest
from botkin.llm.timing import timed
from botkin.parsing.sibr import is_sibr_text
from botkin.parsing.rows import (
    RawAnalysis, merge_dedup, parse_compact_rows, rows_from_raw,
)
from botkin.parsing.text_layer import _verbatim_guard, completeness_guard
from botkin.preprocess.images import prepare_images, to_base64_jpegs
from botkin.preprocess.pdf_text import has_usable_text_layer, open_pdf

log = logging.getLogger(__name__)

_TEXT_EMPTY_RETRIES = 2


def _count_rows(response: BaseModel) -> int:
    """Число распознанных строк: для RawAnalysis — tests[].results + results; иначе .results."""
    n = 0
    for test in getattr(response, "tests", []) or []:
        n += len(getattr(test, "results", []) or [])
    top = getattr(response, "results", []) or []
    return n + len(top)


def _messages_from_text(system_prompt: str, instruction: str, text: str) -> list[dict]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{instruction}\n\n{text}"},
    ]


def call_text(messages: list[dict], doc_name: str, structured: bool | None = None) -> RawAnalysis:
    """Детерминированный (temp=0) вызов структурирования текстового слоя через TEXT_MODEL.

    structured=None → берём TEXT_STRUCTURED_OUTPUT (grammar-constrained). structured=False
    отключает grammar для adaptive-повтора: XGrammar на части входов схлопывает вывод в
    пустой валидный объект, а без грамматики та же модель отдаёт нормальный JSON.
    """
    use_structured = TEXT_STRUCTURED_OUTPUT if structured is None else structured
    log.info(
        "[START_TEXT_EXTRACT] Doc: '%s' | Model: %s | ctx=%d | grammar=%s",
        doc_name, TEXT_MODEL, TEXT_NUM_CTX, "on" if use_structured else "off",
    )
    options = {
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "num_ctx": TEXT_NUM_CTX,
        "repeat_penalty": TEXT_REPEAT_PENALTY,
        "num_predict": TEXT_NUM_PREDICT,
        "temperature": TEXT_LAYER_TEMPERATURE,
    }
    client = get_client(mode=instructor.Mode.JSON)
    try:
        with timed("TEXT_EXTRACT", doc_name) as t:
            t0_call = time.perf_counter()
            response = client.chat.completions.create(
                model=model_name(TEXT_MODEL),
                messages=messages,
                response_model=RawAnalysis,
                max_retries=build_retrying(),
                max_tokens=TEXT_MAX_TOKENS,
                extra_body=build_extra_body(RawAnalysis, options, structured=use_structured),
            )
            elapsed_call = time.perf_counter() - t0_call
            t["metrics"] = metrics_of(response, model_name(TEXT_MODEL), elapsed_call, num_ctx=TEXT_NUM_CTX)
    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError(f"Text extraction failed for {doc_name}: {e}") from e
    log.info(
        "[DONE_TEXT_EXTRACT] Doc: '%s' | Rows: %d | Elapsed: %.2fs",
        doc_name, _count_rows(response), t["elapsed"],
    )
    return response


def call_text_compact(messages: list[dict], doc_name: str) -> list[LabResult]:
    """Компактный построчный вызов TEXT_MODEL: «имя|значение|единица|референс».

    Без JSON-схемы и без grammar: ключи JSON на каждой строке таблицы стоят больше
    токенов, чем данные. Замер на реальных страницах — вызов быстрее в 1.9–2.4 раза
    при том же наборе строк, а на части страниц ещё и обходит коллапс XGrammar в
    пустой валидный объект (см. _TEXT_EMPTY_RETRIES).
    """
    log.info("[START_TEXT_COMPACT] Doc: '%s' | Model: %s | ctx=%d", doc_name, TEXT_MODEL, TEXT_NUM_CTX)
    client = get_raw_client()
    extra_body: dict = {"options": {
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "num_ctx": TEXT_NUM_CTX,
        "repeat_penalty": TEXT_REPEAT_PENALTY,
        "num_predict": TEXT_NUM_PREDICT,
        "temperature": TEXT_LAYER_TEMPERATURE,
    }}
    if os.getenv("VLM_DISABLE_THINKING", "").lower() in ("1", "true", "yes", "on"):
        extra_body["chat_template_kwargs"] = {"enable_thinking": False}
        extra_body["reasoning_effort"] = "none"
        extra_body["options"]["think"] = False
    with timed("TEXT_COMPACT", doc_name) as t:
        t0_call = time.perf_counter()
        response = client.chat.completions.create(
            model=model_name(TEXT_MODEL),
            messages=messages,
            max_tokens=TEXT_MAX_TOKENS,
            temperature=TEXT_LAYER_TEMPERATURE,
            extra_body=extra_body,
        )
        elapsed_call = time.perf_counter() - t0_call
        t["metrics"] = metrics_of(response, model_name(TEXT_MODEL), elapsed_call, num_ctx=TEXT_NUM_CTX)
    content = response.choices[0].message.content
    text = content if isinstance(content, str) else ""
    rows = rows_from_raw(parse_compact_rows(text))
    log.info(
        "[DONE_TEXT_COMPACT] Doc: '%s' | Rows: %d | Elapsed: %.2fs | символов=%d",
        doc_name, len(rows), t["elapsed"], len(text),
    )
    if not rows:
        log.warning("[TEXT_COMPACT_EMPTY] Doc: '%s' | сырой ответ (%d симв.): %s",
                    doc_name, len(text), text[:RAW_LOG_LIMIT] or "<пусто>")
    return rows


def structure_text(lines: list[str], doc_name: str) -> list[LabResult]:
    """Координатные строки → LabResult через TEXT_MODEL (temp=0) + маппинг.

    Adaptive fallback (симметрично картиночному _vlm_extract_with_retry): если structured
    вызов вернул 0 строк — XGrammar мог схлопнуть вывод в пустой объект — повторяем без
    grammar-ограничения. Без ретрая текстовый слой sample_001 флапал ~50% прогонов
    (пустой ответ → мусор от completeness_guard вместо ROMA/Ca125/HE4). Пустой ответ
    случается и без grammar, поэтому unstructured-повтор ограничен _TEXT_EMPTY_RETRIES.
    """
    text = "\n".join(lines)

    if TEXT_COMPACT_OUTPUT:
        try:
            compact_rows = call_text_compact(
                _messages_from_text(ANALYSIS_TEXT_COMPACT_SYSTEM, TEXT_INSTRUCTION, text), doc_name)
            if compact_rows:
                return compact_rows
            log.info("[TEXT_COMPACT_FALLBACK] Doc: '%s' | компакт пуст — повтор по JSON-схеме", doc_name)
        except Exception as e:  # noqa: BLE001 — сетевой/серверный сбой не должен терять страницу
            log.warning("[TEXT_COMPACT_FAILED] Doc: '%s' | %s — повтор по JSON-схеме", doc_name, e)

    messages = _messages_from_text(ANALYSIS_TEXT_SYSTEM, TEXT_INSTRUCTION, text)
    try:
        rows = rows_or_harvest(call_text(messages, doc_name))
        for attempt in range(_TEXT_EMPTY_RETRIES):
            if rows or not TEXT_STRUCTURED_OUTPUT:
                break
            log.info("[TEXT_UNSTRUCTURED_RETRY] Doc: '%s' | structured text пуст (попытка %d) — повтор без grammar",
                     doc_name, attempt + 1)
            rows = rows_or_harvest(call_text(messages, doc_name, structured=False))
        return rows
    except ExtractionError as e:
        return salvage_rows(e)


def should_use_text_layer(source_path) -> bool:
    return source_path.suffix.lower() == ".pdf" and has_usable_text_layer(source_path)


def extract_from_text_layer(
    source_path,
    sibr_ocr_fn: "Callable | None" = None,
    extract_once_fn: "Callable | None" = None,
    androflor_min_rows: int = 0,
    sibr_min_rows: int = 0,
    androflor_retry_long_side: int = 0,
    structure_text_fn: "Callable | None" = None,
) -> list[LabResult] | None:
    """Извлечение из текстового слоя. None → результат слабый, нужен VLM-фолбэк.

    Постранично: каждая страница — отдельный text-LLM вызов, чтобы одинокий результат
    на своей странице (С-реактивный белок без заголовка) не тонул на фоне большой
    таблицы соседней страницы. Затем completeness_guard добирает пропущенные строки.

    СИБР-страницы обрабатываются отдельно: в текстовом слое они содержат только
    заголовок и описание, а цифры — в растровой таблице. Для таких страниц
    делаем специализированный VLM-вызов и склеиваем с обычным текстовым результатом.
    """
    _structure = structure_text_fn or structure_text
    pdf = open_pdf(source_path)
    pages = pdf.pages
    src = pdf.flat_text
    all_lines = [ln for pg in pages for ln in pg]
    log.info(
        "[TEXTLAYER_QUALITY] Doc: '%s' | символов=%d | страниц=%d строк-реконструкции=%d",
        source_path.name, len(src), len(pages), len(all_lines),
    )
    if not all_lines:
        return None

    sibr_page_indices = [i for i, lines in enumerate(pages) if is_sibr_text("\n".join(lines))]

    rows: list[LabResult] = []
    for i, lines in enumerate(pages):
        if i in sibr_page_indices or not lines:
            continue
        page_rows = _structure(lines, f"{source_path.name}#стр{i + 1}")
        rows = merge_dedup(rows, page_rows)
    kept, rejected = _verbatim_guard(rows, src)
    log.info(
        "[VERBATIM_GUARD] Doc: '%s' | принято=%d забраковано=%d",
        source_path.name, len(kept), len(rejected),
    )
    total = len(kept) + len(rejected)
    if total and len(rejected) / total > VERBATIM_MAX_REJECT_RATIO:
        return None

    recovered = completeness_guard(all_lines, kept)
    if recovered:
        log.info(
            "[COMPLETENESS_GUARD] Doc: '%s' | добрано пропущенных строк=%d: %s",
            source_path.name, len(recovered), [r.analyte_name for r in recovered],
        )
        rows = merge_dedup(kept, recovered)
    else:
        rows = kept

    raster_page_indices = sibr_page_indices + [
        i for i, lines in enumerate(pages) if not lines and i not in sibr_page_indices
    ]
    if raster_page_indices and sibr_ocr_fn and extract_once_fn:
        images = prepare_images(
            source_path,
            long_side=IMAGE_EXTRACT_LONG_SIDE,
            upscale=True, deskew=True, enhance=True,
        )
        for i in sibr_page_indices:
            b64 = to_base64_jpegs([images[i]])
            sibr_rows = sibr_ocr_fn(b64, f"{source_path.name}#стр{i + 1}")
            if len(sibr_rows) >= sibr_min_rows:
                log.info("[SIBR_OCR] Doc: '%s' | страница %d | строк=%d", source_path.name, i + 1, len(sibr_rows))
                rows = merge_dedup(rows, sibr_rows)
            else:
                log.info("[SIBR_OCR_SKIP] Doc: '%s' | страница %d | строк=%d", source_path.name, i + 1, len(sibr_rows))
        empty_pages = [i for i, lines in enumerate(pages) if not lines and i not in sibr_page_indices]
        for i in empty_pages:
            b64 = to_base64_jpegs([images[i]])

            def _low_res_fn(_page_index=i):
                low = prepare_images(
                    source_path, long_side=androflor_retry_long_side,
                    upscale=False, deskew=False, enhance=False,
                )
                return to_base64_jpegs([low[_page_index]])

            try:
                page_rows, _ = extract_once_fn(b64, f"{source_path.name}#стр{i + 1}", _low_res_fn)
                if page_rows:
                    log.info("[RASTER_OCR] Doc: '%s' | страница %d | строк=%d", source_path.name, i + 1, len(page_rows))
                    rows = merge_dedup(rows, page_rows)
                else:
                    log.info("[RASTER_OCR_SKIP] Doc: '%s' | страница %d | строк=0", source_path.name, i + 1)
            except ExtractionError as e:
                log.warning("[RASTER_OCR_FAILED] Doc: '%s' | страница %d: %s", source_path.name, i + 1, e)

    if not rows:
        return None
    return rows
