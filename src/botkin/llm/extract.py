"""VLM-извлечение структурированных данных из медицинских документов.

Здесь только оркестрация вызовов модели: подготовка картинок/сообщений, сам вызов
qwen3-vl через instructor, выбор пути (текстовый слой vs VLM) и сшивка результатов.
Весь детерминированный разбор (числа, harvester, текстовый слой, дедуп) вынесен в
botkin.parsing и переиспользуется здесь.
"""
import logging
import time
from pathlib import Path

import instructor
from pydantic import BaseModel

from botkin.config import (
    VLM_MODEL, VLM_TEMPERATURE, VLM_MAX_TOKENS, VLM_MAX_RETRIES, IMAGE_EXTRACT_LONG_SIDE,
    VERBATIM_MAX_REJECT_RATIO, VLM_STRUCTURED_OUTPUT,
)
from botkin.domain.models import LabResult, DoctorReport
from botkin.exceptions import ExtractionError
from botkin.llm.client import get_client, build_extra_body, default_options, usage_of
from botkin.llm.prompts import (
    ANALYSIS_INSTRUCTION, ANALYSIS_TEXT_SYSTEM, ANALYSIS_VLM_SYSTEM,
    DOCTOR_REPORT_INSTRUCTION, DOCTOR_REPORT_VLM_SYSTEM, PROMPTS_VERSION, TEXT_INSTRUCTION,
)
from botkin.parsing.harvester import (
    _collect_tables, harvest_lab_rows, loads_json, salvage_json_objects,
)
from botkin.parsing.rows import RawAnalysis, extraction_quality, merge_dedup, rows_from_raw
from botkin.parsing.scalars import parse_lab_value, parse_reference_range
from botkin.parsing.text_layer import _parse_text_line, _verbatim_guard, completeness_guard
from botkin.preprocess.images import prepare_images, to_base64_jpegs
from botkin.preprocess.pdf_text import (
    has_usable_text_layer, reconstruct_pages, source_text,
)

log = logging.getLogger(__name__)

# Имена под старый интерфейс модуля: часть тестов обращается к ним как к атрибутам
# botkin.llm.extract. Сами реализации живут в botkin.parsing.
_merge_dedup = merge_dedup
_salvage_json_objects = salvage_json_objects
_loads_json = loads_json

__all__ = [
    "run_analysis", "run_doctor_report", "RawAnalysis", "extraction_quality",
    "harvest_lab_rows", "rows_from_raw", "parse_lab_value", "parse_reference_range",
    "completeness_guard", "_parse_text_line", "_verbatim_guard",
]


class DoctorReports(BaseModel):
    results: list[DoctorReport] = []


def _prepare_b64(source_path: Path) -> list[str]:
    """PDF/изображение → список base64-JPEG (по странице) + лог объёма/времени входа."""
    t0 = time.perf_counter()
    b64_images = to_base64_jpegs(prepare_images(
        source_path,
        long_side=IMAGE_EXTRACT_LONG_SIDE,
        upscale=True, deskew=True, enhance=True,
    ))
    prep_s = time.perf_counter() - t0
    total_b64 = sum(len(b) for b in b64_images)
    log.info(
        "[EXTRACT_INPUT] Doc: '%s' | изображений: %d | base64 итого: %d Б (~%d KБ) | препроцессинг: %.2fs",
        source_path.name, len(b64_images), total_b64, total_b64 // 1024, prep_s,
    )
    if not b64_images:
        log.warning("[EXTRACT_INPUT] Doc: '%s' | НЕТ изображений после препроцессинга — VLM нечего анализировать", source_path.name)
    return b64_images


def _messages_from_images(system_prompt: str, instruction: str, b64_images: list[str]) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": instruction}]
    for b64 in b64_images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]


def _build_messages(system_prompt: str, instruction: str, source_path: Path) -> list[dict]:
    return _messages_from_images(system_prompt, instruction, _prepare_b64(source_path))


def _count_rows(response: BaseModel) -> int:
    """Число распознанных строк: для RawAnalysis — tests[].results + results; иначе .results."""
    n = 0
    for test in getattr(response, "tests", []) or []:
        n += len(getattr(test, "results", []) or [])
    top = getattr(response, "results", []) or []
    return n + len(top)


def _raw_content(response: BaseModel) -> str:
    """Сырой текст ответа модели до парсинга (для диагностики «тихого» []). '' если недоступен."""
    try:
        content = response._raw_response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return ""
    return content if isinstance(content, str) else ""


def _call_vlm(messages: list[dict], response_model: type[BaseModel], doc_name: str,
              doc_type: str, options: dict | None = None) -> BaseModel:
    t0 = time.perf_counter()
    log.info("[START_EXTRACT] Doc: '%s' | Type: '%s' | Model: %s", doc_name, doc_type, VLM_MODEL)
    client = get_client(temperature=VLM_TEMPERATURE, mode=instructor.Mode.JSON)
    try:
        response = client.chat.completions.create(
            model=VLM_MODEL,
            messages=messages,
            response_model=response_model,
            max_retries=VLM_MAX_RETRIES,
            max_tokens=VLM_MAX_TOKENS,
            extra_body=build_extra_body(response_model, options),
        )
        elapsed = time.perf_counter() - t0
        prompt_tokens, completion_tokens = usage_of(response)
        n_parsed = _count_rows(response)
        tok_s = completion_tokens / elapsed if elapsed > 0 else 0.0
        log.info(
            "[SUCCESS_EXTRACT] Doc: '%s' | Type: '%s' | Промпты: %s | Схема: %s | "
            "Elapsed: %.2fs | Prompt: %d t | Completion: %d t | %.1f tok/s | Распознано строк: %d",
            doc_name, doc_type, PROMPTS_VERSION, "on" if VLM_STRUCTURED_OUTPUT else "off",
            elapsed, prompt_tokens, completion_tokens, tok_s, n_parsed,
        )
        # Сырой ответ модели — на DEBUG (может быть объёмным). При n_parsed==0 поднимаем до WARNING:
        # это и есть «извлечение вернуло пусто» — самое нужное для диагностики место.
        raw = _raw_content(response)
        if n_parsed == 0:
            log.warning(
                "[EMPTY_EXTRACT] Doc: '%s' | модель вернула 0 строк. Сырой ответ (%d симв.): %s",
                doc_name, len(raw), raw[:4000] or "<пусто/недоступно>",
            )
        else:
            log.debug("[RAW_EXTRACT] Doc: '%s' | сырой ответ (%d симв.): %s", doc_name, len(raw), raw[:4000])
        return response
    except Exception as e:
        elapsed = time.perf_counter() - t0
        log.error("[FAILED_EXTRACT] Doc: '%s' | Type: '%s' | Elapsed: %.2fs | Error: %s", doc_name, doc_type, elapsed, e)
        err = ExtractionError(f"Сбой извлечения ({doc_type}): {e}")
        err.raw_text = _raw_text_from_exc(e)  # сырой ответ для возможного salvage обрезанного JSON
        raise err from e


def _raw_text_from_exc(exc: Exception) -> str:
    """Сырой текст последнего ответа модели из instructor-исключения (для salvage)."""
    raw = getattr(exc, "raw_text", None)
    if isinstance(raw, str) and raw:
        return raw
    comp = getattr(exc, "last_completion", None) or getattr(exc.__cause__, "last_completion", None)
    try:
        content = comp.choices[0].message.content
        return content if isinstance(content, str) else ""
    except (AttributeError, IndexError, TypeError):
        return ""


def _extract_once(b64_images: list[str], doc_name: str) -> tuple[list[LabResult], int]:
    """Один VLM-вызов по набору изображений + гибридный разбор → (строки, число исследований)."""
    messages = _messages_from_images(ANALYSIS_VLM_SYSTEM, ANALYSIS_INSTRUCTION, b64_images)
    try:
        raw = _call_vlm(messages, RawAnalysis, doc_name, "analysis")
    except ExtractionError as e:
        # Обрезанный JSON/таймаут: спасаем полные объекты-строки из сырого ответа.
        objs = salvage_json_objects(_raw_text_from_exc(e))
        rows = harvest_lab_rows(objs) if objs else []
        if rows:
            log.info("[EXTRACT_SALVAGED] Doc: '%s' | из обрезанного ответа спасено строк: %d", doc_name, len(rows))
            return rows, 1
        raise
    rows = rows_from_raw(raw)
    tables_struct = len(raw.tests) + (1 if raw.results else 0)
    if rows:
        return rows, tables_struct
    # Структурный разбор пуст (чужие ключи) → harvester по сырому JSON.
    data = loads_json(_raw_content(raw))
    if data is None:
        return [], tables_struct
    tables: list = []
    _collect_tables(data, tables)
    rows = harvest_lab_rows(data)
    log.info("[EXTRACT_FALLBACK] Doc: '%s' | harvester собрал строк: %d (таблиц: %d)", doc_name, len(rows), len(tables))
    return rows, (len(tables) or tables_struct)


def _messages_from_text(system_prompt: str, instruction: str, text: str) -> list[dict]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{instruction}\n\n{text}"},
    ]


def _call_text(messages: list[dict], doc_name: str) -> RawAnalysis:
    """Детерминированный (temp=0) text-only вызов структурирования."""
    options = {**default_options(), "temperature": 0.0}
    return _call_vlm(messages, RawAnalysis, doc_name, "analysis-text", options=options)


def _structure_text(lines: list[str], doc_name: str) -> list[LabResult]:
    """Координатные строки → LabResult через text-only LLM (temp=0) + маппинг."""
    text = "\n".join(lines)
    messages = _messages_from_text(ANALYSIS_TEXT_SYSTEM, TEXT_INSTRUCTION, text)
    try:
        raw = _call_text(messages, doc_name)
    except ExtractionError as e:
        objs = salvage_json_objects(_raw_text_from_exc(e))
        return harvest_lab_rows(objs) if objs else []
    rows = rows_from_raw(raw)
    if rows:
        return rows
    data = loads_json(_raw_content(raw))
    return harvest_lab_rows(data) if data is not None else []


def _should_use_text_layer(source_path: Path) -> bool:
    return source_path.suffix.lower() == ".pdf" and has_usable_text_layer(source_path)


def _extract_from_text_layer(source_path: Path) -> list[LabResult] | None:
    """Извлечение из текстового слоя. None → результат слабый, нужен VLM-фолбэк.

    Постранично: каждая страница — отдельный text-LLM вызов, чтобы одинокий результат
    на своей странице (С-реактивный белок без заголовка) не тонул на фоне большой
    таблицы соседней страницы. Затем completeness_guard добирает пропущенные строки.
    """
    pages = reconstruct_pages(source_path)
    src = source_text(source_path)
    all_lines = [ln for pg in pages for ln in pg]
    log.info(
        "[TEXTLAYER_QUALITY] Doc: '%s' | символов=%d | страниц=%d строк-реконструкции=%d",
        source_path.name, len(src), len(pages), len(all_lines),
    )
    if not all_lines:
        return None
    rows: list[LabResult] = []
    for i, lines in enumerate(pages):
        if not lines:
            continue
        page_rows = _structure_text(lines, f"{source_path.name}#стр{i + 1}")
        rows = merge_dedup(rows, page_rows)
    # Анти-пропускной добор: строки-результаты слоя, что LLM пропустил.
    recovered = completeness_guard(all_lines, rows)
    if recovered:
        log.info(
            "[COMPLETENESS_GUARD] Doc: '%s' | добрано пропущенных строк=%d: %s",
            source_path.name, len(recovered), [r.analyte_name for r in recovered],
        )
        rows = merge_dedup(rows, recovered)
    if not rows:
        return None
    kept, rejected = _verbatim_guard(rows, src)
    total = len(kept) + len(rejected)
    log.info(
        "[VERBATIM_GUARD] Doc: '%s' | принято=%d забраковано=%d",
        source_path.name, len(kept), len(rejected),
    )
    if total and len(rejected) / total > VERBATIM_MAX_REJECT_RATIO:
        return None
    return kept or None


def _finish(rows: list[LabResult], doc_name: str, t0: float, n_calls: int) -> list[LabResult]:
    """Финальное логирование качества (общий хвост текстового и VLM путей)."""
    q = extraction_quality(rows)
    total_s = time.perf_counter() - t0
    log.info(
        "[EXTRACT_MAPPED] Doc: '%s' | строк: %d | VLM-вызовов: %d | всего: %.2fs",
        doc_name, len(rows), n_calls, total_s,
    )
    log.info(
        "[EXTRACT_QUALITY] Doc: '%s' | строк: %d | с числом: %d | с текстом: %d | "
        "с нормой: %d | с единицей: %d",
        doc_name, q["total"], q["with_value_num"], q["with_value_text"],
        q["with_ref"], q["with_unit"],
    )
    return rows


def run_analysis(source_path: Path) -> list[LabResult]:
    t0 = time.perf_counter()

    # Текстовый слой (цифровые PDF): детерминированно, без галлюцинаций VLM.
    if _should_use_text_layer(source_path):
        rows = _extract_from_text_layer(source_path)
        if rows is not None:
            log.info("[EXTRACT_PATH] Doc: '%s' | путь: text_layer", source_path.name)
            return _finish(rows, source_path.name, t0, n_calls=0)
    log.info("[EXTRACT_PATH] Doc: '%s' | путь: vlm", source_path.name)

    b64_images = _prepare_b64(source_path)
    n_pages = len(b64_images)

    if n_pages <= 1:
        # Одностраничный документ — один вызов.
        try:
            rows, _ = _extract_once(b64_images, source_path.name)
        except ExtractionError as e:
            log.warning("[EXTRACT_FAILED] Doc: '%s' | извлечение пусто: %s", source_path.name, e)
            rows = []
        n_calls = 1
    else:
        # Многостраничный — извлекаем ПОСТРАНИЧНО: каждая страница читается ровно один раз.
        # Раньше был общий вызов по всем страницам + добор каждой → страницы читались дважды
        # (медленно) и модель смешивала/теряла содержимое. Постранично — модель фокусируется
        # на одной странице. Сбой страницы не валит документ; объединяем дедупом по имени.
        log.info("[MULTIPAGE] Doc: '%s' | страниц=%d — извлечение постранично", source_path.name, n_pages)
        rows = []
        n_calls = 0
        for i, page in enumerate(b64_images):
            n_calls += 1
            try:
                page_rows, _ = _extract_once([page], f"{source_path.name}#стр{i + 1}")
            except ExtractionError as e:
                log.warning(
                    "[MULTIPAGE_PAGE_FAILED] Doc: '%s' стр.%d пропущена: %s",
                    source_path.name, i + 1, e,
                )
                continue
            rows = merge_dedup(rows, page_rows)

    return _finish(rows, source_path.name, t0, n_calls)


def run_doctor_report(source_path: Path) -> list[DoctorReport]:
    messages = _build_messages(DOCTOR_REPORT_VLM_SYSTEM, DOCTOR_REPORT_INSTRUCTION, source_path)
    return _call_vlm(messages, DoctorReports, source_path.name, "doctor_report").results
