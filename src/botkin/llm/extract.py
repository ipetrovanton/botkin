"""VLM-извлечение структурированных данных из медицинских документов."""
import logging
import re
import time
from pathlib import Path
from typing import Optional, Union

import instructor
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from botkin.config import VLM_MODEL, VLM_TEMPERATURE, VLM_MAX_TOKENS, IMAGE_EXTRACT_LONG_SIDE
from botkin.domain.models import LabResult, DoctorReport
from botkin.exceptions import ExtractionError
from botkin.llm.client import get_client, default_options
from botkin.llm.prompts import ANALYSIS_VLM_SYSTEM, DOCTOR_REPORT_VLM_SYSTEM
from botkin.preprocess.images import prepare_images, to_base64_jpegs

log = logging.getLogger(__name__)


class DoctorReports(BaseModel):
    results: list[DoctorReport] = []


# ── Сырая схема ответа qwen3-vl для анализов ─────────────────────────────────
# Модель естественно отдаёт вложенную структуру tests[].results[] с полями
# parameter/value/reference_range, а не плоский LabResult. Принимаем её как есть
# (+ алиасы на частые синонимы и top-level results как подстраховку), затем маппим.

class _RawRow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    parameter: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("parameter", "name", "analyte_name", "test_name"))
    value: Optional[Union[str, float, int]] = Field(
        default=None, validation_alias=AliasChoices("value", "result", "value_num"))
    unit: Optional[str] = None
    reference_range: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("reference_range", "reference", "norm", "ref"))
    comment: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("comment", "comments"))


class _RawTest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    test_name: Optional[str] = None
    results: list[_RawRow] = []


class RawAnalysis(BaseModel):
    """Верхний уровень сырого ответа: список тестов и/или плоский список строк."""
    model_config = ConfigDict(extra="ignore")
    tests: list[_RawTest] = []
    results: list[_RawRow] = []


_RANGE_RE = re.compile(r"^(-?\d+(?:[.,]\d+)?)\s*[-–—]\s*(-?\d+(?:[.,]\d+)?)$")
_LE_RE = re.compile(r"^[<≤]\s*(-?\d+(?:[.,]\d+)?)$")
_GE_RE = re.compile(r"^[>≥]\s*(-?\d+(?:[.,]\d+)?)$")


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


def parse_lab_value(value) -> tuple[Optional[float], Optional[str]]:
    """Результат показателя → (value_num, value_text). Одно из них всегда None.

    «40.8»/«217»/«5,4»→число; «44.6*» (флаг выхода за норму)→44.6; текст→value_text.
    """
    if value is None:
        return None, None
    if isinstance(value, (int, float)):
        return float(value), None
    s = str(value).strip()
    if not s:
        return None, None
    # Число, возможно с хвостовым флагом (*, ↑, ↓, стрелки): берём ведущее число.
    m = re.match(r"^(-?\d+(?:[.,]\d+)?)\s*[*↑↓▲▼+\-]?\s*$", s)
    if m:
        return _to_float(m.group(1)), None
    return None, s


def parse_reference_range(ref) -> tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
    """Норма → (ref_low, ref_high, ref_operator, ref_text).

    «35 - 45»→low/high; «< 1.0»→op '<' + high; «> 120»→op '>' + low; «≤/≥»→'<'/'>';
    нечисловая норма→ref_text.
    """
    if ref is None:
        return None, None, None, None
    s = str(ref).strip()
    if not s:
        return None, None, None, None
    m = _RANGE_RE.match(s)
    if m:
        return _to_float(m.group(1)), _to_float(m.group(2)), None, None
    m = _LE_RE.match(s)
    if m:
        return None, _to_float(m.group(1)), "<", None
    m = _GE_RE.match(s)
    if m:
        return _to_float(m.group(1)), None, ">", None
    return None, None, None, s


def rows_from_raw(raw: RawAnalysis) -> list[LabResult]:
    """Уплощает tests[].results[] (+ top-level results) в список LabResult."""
    rows: list[_RawRow] = list(raw.results)
    for test in raw.tests:
        rows.extend(test.results)
    out: list[LabResult] = []
    for r in rows:
        if not r.parameter:
            continue
        value_num, value_text = parse_lab_value(r.value)
        ref_low, ref_high, ref_operator, ref_text = parse_reference_range(r.reference_range)
        out.append(LabResult(
            analyte_name=r.parameter,
            value_num=value_num,
            value_text=value_text,
            value_raw=str(r.value) if r.value is not None else None,
            unit=r.unit,
            ref_low=ref_low,
            ref_high=ref_high,
            ref_operator=ref_operator,
            ref_text=ref_text,
            comments=r.comment,
        ))
    return out


def _build_messages(system_prompt: str, instruction: str, source_path: Path) -> list[dict]:
    b64_images = to_base64_jpegs(prepare_images(
        source_path,
        long_side=IMAGE_EXTRACT_LONG_SIDE,
        upscale=True, deskew=True, enhance=True,
    ))
    total_b64 = sum(len(b) for b in b64_images)
    log.info(
        "[EXTRACT_INPUT] Doc: '%s' | изображений в VLM: %d | base64 итого: %d Б (~%d KБ)",
        source_path.name, len(b64_images), total_b64, total_b64 // 1024,
    )
    if not b64_images:
        log.warning("[EXTRACT_INPUT] Doc: '%s' | НЕТ изображений после препроцессинга — VLM нечего анализировать", source_path.name)
    content: list[dict] = [{"type": "text", "text": instruction}]
    for b64 in b64_images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]


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


def _call_vlm(messages: list[dict], response_model: type[BaseModel], doc_name: str, doc_type: str) -> BaseModel:
    t0 = time.perf_counter()
    log.info("[START_EXTRACT] Doc: '%s' | Type: '%s' | Model: %s", doc_name, doc_type, VLM_MODEL)
    client = get_client(temperature=VLM_TEMPERATURE, mode=instructor.Mode.JSON)
    try:
        response = client.chat.completions.create(
            model=VLM_MODEL,
            messages=messages,
            response_model=response_model,
            max_retries=2,
            max_tokens=VLM_MAX_TOKENS,
            extra_body={"options": default_options()},
        )
        elapsed = time.perf_counter() - t0
        usage = response._raw_response.usage
        n_parsed = _count_rows(response)
        log.info(
            "[SUCCESS_EXTRACT] Doc: '%s' | Type: '%s' | Elapsed: %.2fs | "
            "Prompt: %d t | Completion: %d t | Распознано строк: %d",
            doc_name, doc_type, elapsed, usage.prompt_tokens, usage.completion_tokens, n_parsed,
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
        raise ExtractionError(f"Сбой извлечения ({doc_type}): {e}") from e


def run_analysis(source_path: Path) -> list[LabResult]:
    messages = _build_messages(ANALYSIS_VLM_SYSTEM, "Extract lab results from these document images.", source_path)
    raw = _call_vlm(messages, RawAnalysis, source_path.name, "analysis")
    return rows_from_raw(raw)


def run_doctor_report(source_path: Path) -> list[DoctorReport]:
    messages = _build_messages(DOCTOR_REPORT_VLM_SYSTEM, "Extract doctor reports from these document images.", source_path)
    return _call_vlm(messages, DoctorReports, source_path.name, "doctor_report").results
