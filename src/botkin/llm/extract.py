"""VLM-извлечение структурированных данных из медицинских документов."""
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional, Union

import instructor
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from botkin.config import (
    VLM_MODEL, VLM_TEMPERATURE, VLM_MAX_TOKENS, IMAGE_EXTRACT_LONG_SIDE,
    VERBATIM_MAX_REJECT_RATIO,
)
from botkin.domain.models import LabResult, DoctorReport
from botkin.exceptions import ExtractionError
from botkin.llm.client import get_client, default_options
from botkin.llm.prompts import ANALYSIS_VLM_SYSTEM, DOCTOR_REPORT_VLM_SYSTEM, ANALYSIS_TEXT_SYSTEM
from botkin.preprocess.images import prepare_images, to_base64_jpegs
from botkin.preprocess.pdf_text import (
    has_usable_text_layer, reconstruct_lines, source_text,
)

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

    Берём ВЕДУЩЕЕ число: «40.8»/«217»/«5,4»→число; «44.6*» (флаг)→44.6;
    «40.8%»/«9 мм/ч» (вклеенная единица)→число; нечисловой текст→value_text.
    """
    if value is None:
        return None, None
    if isinstance(value, (int, float)):
        return float(value), None
    s = str(value).strip()
    if not s:
        return None, None
    m = re.match(r"^[<>≤≥]", s)  # это оператор нормы, не результат — не число
    if m:
        return None, s
    m = re.match(r"^(-?\d+(?:[.,]\d+)?)", s)  # ведущее число, хвост (флаг/единица) отбрасываем
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
    return _dedup_rows(out)


# ── Harvester по содержимому (fallback к структурному разбору) ────────────────
# qwen3-vl каждый прогон меняет имена ключей (англ/рус) и обёртку. Harvester
# не полагается на фиксированные имена: распознаёт роль поля по алиасу ключа
# (по подстроке, рус+англ), а при незнакомом ключе — по виду значения.

_KEY_COMMENT = ("comment", "коммент", "примечан")
_KEY_REF = ("reference", "range", "норма", "норматив", "диапазон", "ref", "norm")
_KEY_UNIT = ("unit", "единиц", "ед.изм", "ед_изм", "размерност")
_KEY_VALUE = ("value", "result", "результат", "значен")
_KEY_NAME = ("parameter", "name", "analyte", "показател", "исследован", "параметр",
             "наименован", "тест", "test")


def _key_role(key: str) -> Optional[str]:
    """Роль поля по имени ключа. Порядок важен: ref/unit/comment до value/name."""
    k = " ".join(str(key).strip().lower().replace("ё", "е").split())
    if not k:
        return None
    if any(t in k for t in _KEY_COMMENT):
        return "comment"
    if any(t in k for t in _KEY_REF):
        return "ref"
    if any(t in k for t in _KEY_UNIT):
        return "unit"
    if any(t in k for t in _KEY_VALUE):
        return "value"
    if any(t in k for t in _KEY_NAME):
        return "name"
    return None


def _looks_like_ref(s: str) -> bool:
    return bool(_RANGE_RE.match(s) or _LE_RE.match(s) or _GE_RE.match(s))


def _looks_like_number(s: str) -> bool:
    return bool(re.match(r"^-?\d", s.strip()))


def _harvest_row(d: dict) -> Optional[LabResult]:
    """Одна строка-показатель (dict с произвольными именами полей) → LabResult."""
    scalars = [(str(k), str(v).strip()) for k, v in d.items()
               if v is not None and not isinstance(v, (list, dict)) and str(v).strip()]
    if not scalars:
        return None

    name = value_str = unit = ref = comment = None
    taken: set[int] = set()
    # 1) по ролям ключей
    for i, (k, s) in enumerate(scalars):
        role = _key_role(k)
        if role == "name" and name is None:
            name, _ = s, taken.add(i)
        elif role == "value" and value_str is None:
            value_str, _ = s, taken.add(i)
        elif role == "unit" and unit is None:
            unit, _ = s, taken.add(i)
        elif role == "ref" and ref is None:
            ref, _ = s, taken.add(i)
        elif role == "comment" and comment is None:
            comment, _ = s, taken.add(i)
    # 2) добор по содержимому из незанятых полей
    if ref is None:
        for i, (k, s) in enumerate(scalars):
            if i not in taken and _looks_like_ref(s):
                ref, _ = s, taken.add(i)
                break
    if value_str is None:
        for i, (k, s) in enumerate(scalars):
            if i not in taken and _looks_like_number(s):
                value_str, _ = s, taken.add(i)
                break
    if name is None:
        cand = [(i, s) for i, (k, s) in enumerate(scalars)
                if i not in taken and not _looks_like_number(s)]
        if cand:
            i, name = max(cand, key=lambda x: len(x[1]))
            taken.add(i)
    if not name:
        return None

    value_num, value_text = parse_lab_value(value_str)
    ref_low, ref_high, ref_operator, ref_text = parse_reference_range(ref)
    return LabResult(
        analyte_name=name, value_num=value_num, value_text=value_text,
        value_raw=value_str, unit=unit,
        ref_low=ref_low, ref_high=ref_high, ref_operator=ref_operator, ref_text=ref_text,
        comments=comment,
    )


def _is_row_dict(d) -> bool:
    """dict «похож на строку показателя»: ≥2 скаляра и есть значение/норма (по виду или ключу)."""
    if not isinstance(d, dict):
        return False
    scal = [(k, v) for k, v in d.items() if not isinstance(v, (list, dict))]
    if len(scal) < 2:
        return False
    by_content = any(
        v is not None and (_looks_like_number(str(v)) or _looks_like_ref(str(v)))
        for k, v in scal
    )
    by_key = any(_key_role(str(k)) in ("value", "ref") for k, v in scal)
    return by_content or by_key


def _collect_tables(node, out: list) -> None:
    """Рекурсивно ищет списки строк-показателей в произвольном JSON."""
    if isinstance(node, list):
        rows = [x for x in node if _is_row_dict(x)]
        dicts = [x for x in node if isinstance(x, dict)]
        if rows and len(rows) == len(dicts):
            out.append(rows)
        else:
            for x in node:
                _collect_tables(x, out)
    elif isinstance(node, dict):
        for v in node.values():
            _collect_tables(v, out)


def harvest_lab_rows(data) -> list[LabResult]:
    """Сырой JSON ответа модели (любой структуры) → список LabResult по содержимому."""
    tables: list = []
    _collect_tables(data, tables)
    out: list[LabResult] = []
    for table in tables:
        for item in table:
            row = _harvest_row(item)
            if row is not None:
                out.append(row)
    return _dedup_rows(out)


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
            max_retries=2,
            max_tokens=VLM_MAX_TOKENS,
            extra_body={"options": options or default_options()},
        )
        elapsed = time.perf_counter() - t0
        usage = response._raw_response.usage
        n_parsed = _count_rows(response)
        tok_s = usage.completion_tokens / elapsed if elapsed > 0 else 0.0
        log.info(
            "[SUCCESS_EXTRACT] Doc: '%s' | Type: '%s' | Elapsed: %.2fs | "
            "Prompt: %d t | Completion: %d t | %.1f tok/s | Распознано строк: %d",
            doc_name, doc_type, elapsed, usage.prompt_tokens, usage.completion_tokens, tok_s, n_parsed,
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


def _loads_json(text: str):
    """Толерантный json.loads сырого ответа модели. None, если не разобрать."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _salvage_json_objects(text: str) -> list[dict]:
    """Извлекает все сбалансированные {...}-объекты из (возможно оборванного) текста.

    qwen3-vl при зацикливании упирается в num_predict и обрывает JSON на полуслове —
    весь ответ невалиден, json.loads падает. Но полные объекты-строки ДО обрыва
    валидны: сканируем по балансу скобок (учитывая строки/escape), парсим каждый
    закрытый объект отдельно. harvester затем соберёт из них показатели.
    """
    objs: list[dict] = []
    stack: list[int] = []
    in_str = esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            start = stack.pop()
            try:
                obj = json.loads(text[start:i + 1])
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict):
                objs.append(obj)
    return objs


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


_ANALYSIS_INSTRUCTION = "Extract lab results from these document images."


def _extract_once(b64_images: list[str], doc_name: str) -> tuple[list[LabResult], int]:
    """Один VLM-вызов по набору изображений + гибридный разбор → (строки, число исследований)."""
    messages = _messages_from_images(ANALYSIS_VLM_SYSTEM, _ANALYSIS_INSTRUCTION, b64_images)
    try:
        raw = _call_vlm(messages, RawAnalysis, doc_name, "analysis")
    except ExtractionError as e:
        # Обрезанный JSON/таймаут: спасаем полные объекты-строки из сырого ответа.
        objs = _salvage_json_objects(_raw_text_from_exc(e))
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
    data = _loads_json(_raw_content(raw))
    if data is None:
        return [], tables_struct
    tables: list = []
    _collect_tables(data, tables)
    rows = harvest_lab_rows(data)
    log.info("[EXTRACT_FALLBACK] Doc: '%s' | harvester собрал строк: %d (таблиц: %d)", doc_name, len(rows), len(tables))
    return rows, (len(tables) or tables_struct)


_TEXT_INSTRUCTION = "Размести эти строки лабораторного бланка по колонкам."


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
    messages = _messages_from_text(ANALYSIS_TEXT_SYSTEM, _TEXT_INSTRUCTION, text)
    try:
        raw = _call_text(messages, doc_name)
    except ExtractionError as e:
        objs = _salvage_json_objects(_raw_text_from_exc(e))
        return harvest_lab_rows(objs) if objs else []
    rows = rows_from_raw(raw)
    if rows:
        return rows
    data = _loads_json(_raw_content(raw))
    return harvest_lab_rows(data) if data is not None else []


def extraction_quality(items: list[LabResult]) -> dict:
    """Сводка качества извлечения — для сравнения конфигов (полнота полей)."""
    return {
        "total": len(items),
        "with_value_num": sum(1 for i in items if i.value_num is not None),
        "with_value_text": sum(1 for i in items if i.value_text),
        "with_ref": sum(1 for i in items if i.ref_low is not None
                        or i.ref_high is not None or i.ref_text),
        "with_unit": sum(1 for i in items if i.unit),
    }


_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def _num_tokens(*values) -> list[str]:
    """Нормализованные числовые токены из значений (запятая→точка, без хвостовых .0)."""
    out: list[str] = []
    for v in values:
        if v is None:
            continue
        for m in _NUM_RE.findall(str(v)):
            s = m.replace(",", ".")
            if s.endswith(".0"):
                s = s[:-2]
            out.append(s)
    return out


def _verbatim_guard(rows: list[LabResult], source_text: str):
    """Делит строки на (kept, rejected): каждое число строки обязано быть в source_text.

    Числа источника собираем в множество нормализованных токенов; строка проходит,
    если ВСЕ её числа (value_raw + границы референса) присутствуют в источнике.
    """
    source_nums = set(_num_tokens(source_text))
    kept: list[LabResult] = []
    rejected: list[LabResult] = []
    for r in rows:
        tokens = _num_tokens(r.value_raw, r.ref_low, r.ref_high, r.ref_text)
        if all(t in source_nums for t in tokens):
            kept.append(r)
        else:
            rejected.append(r)
    return kept, rejected


def _row_key(r: LabResult):
    return (r.analyte_name.strip().lower(), r.value_num, r.value_text)


def _dedup_rows(rows: list[LabResult]) -> list[LabResult]:
    """Схлопывает одинаковые (имя, значение) строки, сохраняя порядок первого вхождения.

    qwen3-vl при зацикливании повторяет показатели (MCV/MCH/MCHC/RDW дважды) — это
    раздувает ответ до num_predict и грозит обрывом JSON. Дедуп на выходе разбора.
    """
    seen: set = set()
    out: list[LabResult] = []
    for r in rows:
        key = _row_key(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _name_key(r: LabResult) -> str:
    """Ключ показателя по имени (без значения): lower, ё→е, схлопывание пробелов."""
    return " ".join(r.analyte_name.strip().lower().replace("ё", "е").split())


def _merge_dedup(base: list[LabResult], extra: list[LabResult]) -> list[LabResult]:
    """Сливает добор постранично с общим вызовом, дедуп по ИМЕНИ показателя.

    Модель недетерминирована в значениях: один показатель в общем вызове и в доборе
    может иметь разные числа (Гемоглобин 13.7 г/дл vs 143 г/л). Дедуп по (имя,значение)
    оставлял бы оба — дубли с противоречием. Ключ по имени: повтор отбрасываем (доверяем
    первому/общему проходу), добавляем только реально новые показатели (потерянная страница).
    """
    seen = {_name_key(r) for r in base}
    out = list(base)
    for r in extra:
        key = _name_key(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _should_use_text_layer(source_path: Path) -> bool:
    return source_path.suffix.lower() == ".pdf" and has_usable_text_layer(source_path)


def _extract_from_text_layer(source_path: Path) -> list[LabResult] | None:
    """Извлечение из текстового слоя. None → результат слабый, нужен VLM-фолбэк."""
    lines = reconstruct_lines(source_path)
    src = source_text(source_path)
    log.info(
        "[TEXTLAYER_QUALITY] Doc: '%s' | символов=%d | строк-реконструкции=%d",
        source_path.name, len(src), len(lines),
    )
    if not lines:
        return None
    rows = _structure_text(lines, source_path.name)
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
    """Финальное логирование качества (DRY: общий хвост текстового и VLM путей)."""
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
            rows = _merge_dedup(rows, page_rows)

    return _finish(rows, source_path.name, t0, n_calls)


def run_doctor_report(source_path: Path) -> list[DoctorReport]:
    messages = _build_messages(DOCTOR_REPORT_VLM_SYSTEM, "Extract doctor reports from these document images.", source_path)
    return _call_vlm(messages, DoctorReports, source_path.name, "doctor_report").results
