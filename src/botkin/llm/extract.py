"""VLM-извлечение структурированных данных из медицинских документов.

Здесь только оркестрация вызовов модели: подготовка картинок/сообщений, сам вызов
qwen3-vl через instructor, выбор пути (текстовый слой vs VLM) и сшивка результатов.
Весь детерминированный разбор (числа, harvester, текстовый слой, дедуп) вынесен в
botkin.parsing и переиспользуется здесь.
"""
import logging
import time
from pathlib import Path
from typing import Callable

import instructor
from pydantic import BaseModel

from botkin.config import (
    VLM_MODEL, VLM_TEMPERATURE, VLM_MAX_TOKENS, VLM_NUM_CTX, IMAGE_EXTRACT_LONG_SIDE,
    VLM_STRUCTURED_OUTPUT, RAW_LOG_LIMIT,
)
from botkin.domain.models import LabResult, DoctorReport
from botkin.exceptions import ExtractionError
from botkin.llm.client import (
    get_client, build_extra_body, build_retrying, default_options, usage_of, model_name,
)
from botkin.llm.metrics import metrics_of
from botkin.llm.prompts import (
    ANALYSIS_INSTRUCTION, ANALYSIS_VLM_SYSTEM,
    DOCTOR_REPORT_INSTRUCTION, DOCTOR_REPORT_VLM_SYSTEM, PROMPTS_VERSION,
)
from botkin.llm.salvage import (
    raw_text_from_exc as _raw_text_from_exc,
    salvage_rows as _salvage_rows,
    _raw_content,
)
from botkin.llm.text_extract import (
    _count_rows,
    structure_text as _structure_text,
    should_use_text_layer as _should_use_text_layer,
    extract_from_text_layer as _extract_from_text_layer,
)
from botkin.llm.unit_correction import correct_units as _correct_units
from botkin.llm.timing import timed

from botkin.parsing.androflor import is_androflor_text, parse_androflor_ocr
from botkin.parsing.sibr import is_sibr_text
from botkin.parsing.harvester import (
    _collect_tables, harvest_lab_rows, loads_json, salvage_json_objects,
)
from botkin.parsing.rows import (
    RawAnalysis, extraction_quality, filter_noise_rows, merge_dedup, rows_from_raw,
)
from botkin.parsing.scalars import parse_lab_value, parse_reference_range
from botkin.parsing.text_layer import _parse_text_line, _verbatim_guard, completeness_guard
from botkin.preprocess.images import prepare_images, to_base64_jpegs

from botkin.llm.image_ocr import (
    messages_from_images as _messages_from_images,
    call_image_ocr as _call_image_ocr,
)
from botkin.llm.sibr_ocr import (
    sibr_ocr_with_voting as _sibr_ocr_with_voting,
    _SIBR_MIN_ROWS,
)
from botkin.llm.androflor_ocr import (
    androflor_voting as _androflor_voting,
    _ANDROFLOR_MIN_ROWS,
    _ANDROFLOR_RETRY_LONG_SIDE,
)

log = logging.getLogger(__name__)

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


def _build_messages(system_prompt: str, instruction: str, source_path: Path) -> list[dict]:
    return _messages_from_images(system_prompt, instruction, _prepare_b64(source_path))


def _call_vlm(messages: list[dict], response_model: type[BaseModel], doc_name: str,
              doc_type: str, options: dict | None = None,
              structured: bool | None = None) -> BaseModel:
    log.info("[START_EXTRACT] Doc: '%s' | Type: '%s' | Model: %s", doc_name, doc_type, VLM_MODEL)
    client = get_client(mode=instructor.Mode.JSON)
    # Температура из конфига: без неё Ollama берёт свой дефолт, и извлечение флуктуирует.
    if options is None:
        options = {**default_options(), "temperature": VLM_TEMPERATURE}
    t0_outer = time.perf_counter()
    try:
        with timed("EXTRACT", doc_name) as t:
            t0_call = time.perf_counter()
            response = client.chat.completions.create(
                model=model_name(VLM_MODEL),
                messages=messages,
                response_model=response_model,
                max_retries=build_retrying(),
                max_tokens=VLM_MAX_TOKENS,
                extra_body=build_extra_body(response_model, options, structured),
            )
            elapsed_call = time.perf_counter() - t0_call
            t["metrics"] = metrics_of(response, model_name(VLM_MODEL), elapsed_call, num_ctx=VLM_NUM_CTX)

        elapsed = t["elapsed"]
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
        # это и есть "извлечение вернуло пусто" — самое нужное для диагностики место.
        raw = _raw_content(response)
        if n_parsed == 0:
            log.warning(
                "[EMPTY_EXTRACT] Doc: '%s' | модель вернула 0 строк. Сырой ответ (%d симв.): %s",
                doc_name, len(raw), raw[:RAW_LOG_LIMIT] or "<пусто/недоступно>",
            )
        else:
            log.debug("[RAW_EXTRACT] Doc: '%s' | сырой ответ (%d симв.): %s", doc_name, len(raw), raw[:RAW_LOG_LIMIT])
        return response
    except Exception as e:
        elapsed = time.perf_counter() - t0_outer
        log.error("[FAILED_EXTRACT] Doc: '%s' | Type: '%s' | Elapsed: %.2fs | Error: %s", doc_name, doc_type, elapsed, e)
        err = ExtractionError(f"Сбой извлечения ({doc_type}): {e}")
        err.raw_text = _raw_text_from_exc(e)  # сырой ответ для возможного salvage обрезанного JSON
        raise err from e



def _vlm_extract_attempt(
    messages: list[dict], doc_name: str, structured: bool | None = None,
) -> tuple[list[LabResult], int]:
    """Один VLM-вызов + гибридный разбор (структурный → harvester) → (строки, число исследований)."""
    try:
        raw = _call_vlm(messages, RawAnalysis, doc_name, "analysis", structured=structured)
    except ExtractionError as e:
        # Обрезанный JSON/таймаут: спасаем полные объекты-строки из сырого ответа.
        rows = _salvage_rows(e)
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


def _ocr_then_structure(
    b64_images: list[str], doc_name: str,
    low_res_retry_fn: "Callable[[], list[str]] | None" = None,
) -> tuple[list[LabResult], int]:
    """OCR-первичный путь: модель читает таблицу как ТЕКСТ (без grammar), затем
    детерминированное структурирование.

    Обоснование — замер 5 моделей в OCR-режиме (журнал, Итерация 25): qwen3-vl читает
    плотные растровые таблицы (Андрофлор, Тонус) корректно и стабильно, тогда как
    structured output/grammar тем же весом схлопывает вывод в пусто/мусор. Андрофлор с
    Lg-нотацией разбираем доменным parser'ом (без LLM), остальное — общим text-LLM
    структурированием _structure_text (тот же путь, что и для текстового слоя PDF).

    low_res_retry_fn — ленивый рендер той же страницы в меньшем разрешении без
    upscale/enhance для androflor-retry (см. _ANDROFLOR_RETRY_LONG_SIDE); вызывается
    только если основной проход не набрал минимум строк, чтобы не делать лишний рендер
    на документах, где Андрофлор-путь не нужен.
    """
    text = _call_image_ocr(b64_images, doc_name)
    if not text.strip():
        return [], 0
    if is_androflor_text(text):
        rows = parse_androflor_ocr(text)
        # Мульти-вызов + voting: PaddleOCR-VL стохастичен — один прогон даёт 2 строки,
        # другой 11. Voting вынесен в androflor_ocr.androflor_voting.
        rows = _androflor_voting(b64_images, doc_name, rows, low_res_retry_fn)
        # Минимум строк: настоящая таблица Андрофлор даёт ~20 строк, тогда как страница-описание
        # бланка (тоже содержит маркеры «Андрофлор»/«Lactobacillus») при жадном разборе отдаёт
        # 0–1 мусорную строку из прозы/ссылок. Принимаем доменный разбор только если строк
        # достаточно; иначе НЕ уходим в _structure_text (он портит Lg-нотацию "10 5.7" в 10.0),
        # а отдаём пусто — страница-описание не должна вносить ложных показателей.
        if len(rows) >= _ANDROFLOR_MIN_ROWS:
            log.info("[ANDROFLOR_OCR] Doc: '%s' | строк=%d", doc_name, len(rows))
            return rows, len(rows)
        log.info("[ANDROFLOR_OCR_SKIP] Doc: '%s' | не таблица Андрофлор (строк=%d) — пропуск", doc_name, len(rows))
        return [], 0
    if is_sibr_text(text):
        rows = _sibr_ocr_with_voting(b64_images, doc_name)
        if len(rows) >= _SIBR_MIN_ROWS:
            log.info("[SIBR_OCR] Doc: '%s' | строк=%d", doc_name, len(rows))
            return rows, len(rows)
        log.info("[SIBR_OCR_SKIP] Doc: '%s' | не таблица СИБР (строк=%d) — пропуск", doc_name, len(rows))
        return [], 0
    ocr_lines = text.splitlines()
    structured_rows = _structure_text(ocr_lines, doc_name)
    log.info("[OCR_STRUCTURE] Doc: '%s' | строк=%d", doc_name, len(structured_rows))
    # Анти-пропускной добор: OCR-текст — тот же источник, что и текстовый слой PDF,
    # только полученный через VLM. completeness_guard добирает строки, которые
    # text-LLM пропустил при структурировании (например, СОЭ на растровом бланке).
    recovered = completeness_guard(ocr_lines, structured_rows)
    if recovered:
        log.info(
            "[OCR_COMPLETENESS_GUARD] Doc: '%s' | добрано пропущенных строк=%d: %s",
            doc_name, len(recovered), [r.analyte_name for r in recovered],
        )
    rows = merge_dedup(structured_rows, recovered)
    # Возвращаем число строк от text-LLM (до добора) — нужен _extract_once для
    # решения: вызывать ли structured VLM как дополнительный источник. text-LLM
    # может провалить структурирование (0 строк), и completeness_guard доберёт
    # только то, что есть в OCR-тексте. Если OCR-текст неполон (модель не видит
    # часть бланка), structured VLM может найти пропущенное напрямую по схеме.
    return rows, len(structured_rows)


def _vlm_extract_with_retry(b64_images: list[str], doc_name: str) -> tuple[list[LabResult], int]:
    """Structured VLM-извлечение с adaptive fallback на свободный JSON.

    Если structured output (XGrammar) вернул 0 строк — повторяем без grammar constraint.
    XGrammar на плотных/сложных картинках может свалиться в пустой, но валидный по схеме
    объект за 1–2s; без принудительной грамматики модель генерирует свободный JSON,
    который instructor всё равно парсит и валидирует по той же схеме.
    """
    messages = _messages_from_images(ANALYSIS_VLM_SYSTEM, ANALYSIS_INSTRUCTION, b64_images)
    rows, tables = _vlm_extract_attempt(messages, doc_name)
    if not rows and VLM_STRUCTURED_OUTPUT:
        log.info("[EXTRACT_UNSTRUCTURED_RETRY] Doc: '%s' | structured VLM пуст — повтор без grammar", doc_name)
        rows, tables = _vlm_extract_attempt(messages, doc_name, structured=False)
    return rows, tables


def _supplement_with_vlm(ocr_rows: list[LabResult], b64_images: list[str], doc_name: str) -> list[LabResult]:
    """Добор показателей прямым VLM-чтением изображения к строкам из OCR-текста.

    Нужен, когда text-LLM провалил структурирование (все строки — от completeness_guard),
    а значит OCR-текст мог быть неполон: модель не «увидела» часть бланка. Structured VLM
    читает картинку напрямую по схеме и может найти пропущенное (например, СОЭ внизу бланка).
    """
    log.info("[EXTRACT_VLM_SUPPLEMENT] Doc: '%s' | text-LLM 0 строк — structured VLM как доп. источник", doc_name)
    vlm_rows, _ = _vlm_extract_with_retry(b64_images, doc_name)
    if not vlm_rows:
        return ocr_rows
    merged = merge_dedup(ocr_rows, vlm_rows)
    log.info("[EXTRACT_VLM_SUPPLEMENTED] Doc: '%s' | OCR=%d + VLM=%d → объединено=%d",
             doc_name, len(ocr_rows), len(vlm_rows), len(merged))
    return merged


def _extract_once(
    b64_images: list[str], doc_name: str,
    low_res_retry_fn: "Callable[[], list[str]] | None" = None,
) -> tuple[list[LabResult], int]:
    """Один логический проход извлечения по странице(ам) → (строки, число таблиц/исследований).

    OCR-первичный путь (OCR-текст → детерминированное структурирование) с фолбэком на
    structured VLM. Замер моделей (журнал, Итерация 25) показал: универсальный structured
    output/grammar нестабилен на плотных растровых таблицах, тогда как чтение тем же
    qwen3-vl в OCR-режиме корректно. Structured VLM остаётся фолбэком для бланков, где
    свободный OCR-текст структурировать труднее, чем извлекать напрямую по схеме.
    """
    structured_count = 0
    try:
        rows, structured_count = _ocr_then_structure(b64_images, doc_name, low_res_retry_fn)
    except Exception as e:  # noqa: BLE001 — OCR-вызов вне instructor-ретраев; падение → фолбэк на VLM
        log.warning("[OCR_PRIMARY_FAILED] Doc: '%s' | OCR-путь упал, фолбэк на VLM: %s", doc_name, e)
        rows = []
    if rows:
        # text-LLM дал 0 строк (всё добрано completeness_guard) → OCR-текст мог быть
        # неполон, просим structured VLM прочитать картинку напрямую и добрать.
        if structured_count == 0:
            rows = _supplement_with_vlm(rows, b64_images, doc_name)
        return rows, 1
    log.info("[EXTRACT_VLM_FALLBACK] Doc: '%s' | OCR-путь пуст — structured VLM", doc_name)
    return _vlm_extract_with_retry(b64_images, doc_name)



def _finish(rows: list[LabResult], doc_name: str, t0: float, n_calls: int) -> list[LabResult]:
    """Финальное логирование качества (общий хвост текстового и VLM путей)."""
    before = len(rows)
    rows = filter_noise_rows(rows)
    if len(rows) < before:
        log.info(
            "[EXTRACT_NOISE_FILTER] Doc: '%s' | отброшено шапок/мусора: %d",
            doc_name, before - len(rows),
        )
    rows = _correct_units(rows)
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
        rows = _extract_from_text_layer(
            source_path,
            sibr_ocr_fn=_sibr_ocr_with_voting,
            extract_once_fn=_extract_once,
            androflor_min_rows=_ANDROFLOR_MIN_ROWS,
            sibr_min_rows=_SIBR_MIN_ROWS,
            androflor_retry_long_side=_ANDROFLOR_RETRY_LONG_SIDE,
            structure_text_fn=_structure_text,
        )
        if rows is not None:
            log.info("[EXTRACT_PATH] Doc: '%s' | путь: text_layer", source_path.name)
            return _finish(rows, source_path.name, t0, n_calls=0)
    log.info("[EXTRACT_PATH] Doc: '%s' | путь: vlm", source_path.name)

    b64_images = _prepare_b64(source_path)
    n_pages = len(b64_images)

    def _low_res_retry_fn(page_index: int | None) -> Callable[[], list[str]]:
        def _render() -> list[str]:
            images = prepare_images(
                source_path, long_side=_ANDROFLOR_RETRY_LONG_SIDE,
                upscale=False, deskew=False, enhance=False,
            )
            if page_index is not None:
                images = [images[page_index]]
            return to_base64_jpegs(images)
        return _render

    if n_pages <= 1:
        # Одностраничный документ — один вызов.
        try:
            rows, _ = _extract_once(b64_images, source_path.name, _low_res_retry_fn(None))
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
                page_rows, _ = _extract_once([page], f"{source_path.name}#стр{i + 1}", _low_res_retry_fn(i))
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
