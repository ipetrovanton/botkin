"""Детерминированные факты из лабораторных результатов.

Модуль намеренно не делает медицинских выводов вроде «это анемия». Он вычисляет только
то, что следует из самого бланка: значение ниже, выше или внутри указанного референса.
Диагнозы, причинно-следственные связи и рекомендации остаются отдельным слоем.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping

from botkin.parsing.constants import RANGE_SEARCH_RE, UPPER_SEARCH_RE, LOWER_SEARCH_RE


@dataclass(frozen=True)
class LabFact:
    """Проверяемый факт об одном лабораторном результате."""

    name: str
    value_num: float | None
    unit: str | None
    reference: str
    status: str


_STATUS_LABELS = {
    "low": "ниже референса",
    "high": "выше референса",
    "normal": "в пределах референса",
    "unknown": "референс не позволяет сравнить численно",
}


def classify_value(
    value_num: float | None,
    ref_low: float | None,
    ref_high: float | None,
) -> str:
    """Классифицирует число только по переданным границам референса."""
    if value_num is None or (ref_low is None and ref_high is None):
        return "unknown"
    if ref_low is not None and value_num < ref_low:
        return "low"
    if ref_high is not None and value_num > ref_high:
        return "high"
    return "normal"




def parse_reference_range(ref_text: object) -> tuple[float | None, float | None]:
    """Извлекает числовые границы (low, high) из текстового референса.

    Многие бланки кладут диапазон в свободный текст ("220 - 450 umol/L"), а не в
    отдельные числовые поля. Без разбора такие показатели нельзя сравнить с нормой.

    Ограничение: сравнение чисел корректно только когда единица значения совпадает с
    единицей в референсе. Конвертацию единиц парсер не делает и делать не должен.
    """
    text = _as_text(ref_text)
    if not text:
        return (None, None)
    match = RANGE_SEARCH_RE.search(text)
    if match:
        return (_num(match.group(1)), _num(match.group(2)))
    match = UPPER_SEARCH_RE.search(text)
    if match:
        return (None, _num(match.group(1)))
    match = LOWER_SEARCH_RE.search(text)
    if match:
        return (_num(match.group(1)), None)
    return (None, None)


def build_lab_facts(rows: Iterable[Mapping[str, object]]) -> list[LabFact]:
    """Преобразует строки БД/API в детерминированные лабораторные факты."""
    facts: list[LabFact] = []
    for row in rows:
        name = str(row.get("name") or row.get("analyte_name") or "").strip()
        if not name:
            continue
        value_num = _as_float(row.get("value_num"))
        raw_low = _as_float(row.get("ref_low"))
        raw_high = _as_float(row.get("ref_high"))
        ref_low, ref_high = raw_low, raw_high
        if ref_low is None and ref_high is None:
            ref_low, ref_high = parse_reference_range(row.get("ref_text"))
        status = classify_value(value_num, ref_low, ref_high)
        facts.append(LabFact(
            name=name,
            value_num=value_num,
            unit=_as_text(row.get("unit")),
            reference=_format_reference(raw_low, raw_high, row.get("ref_text")),
            status=status,
        ))
    return facts


def render_lab_facts(facts: Iterable[LabFact]) -> str:
    """Рендерит факты в компактный контекст для UI или LLM."""
    lines = ["ДЕТЕРМИНИРОВАННЫЕ ФАКТЫ (только сравнение с референсом):"]
    for fact in facts:
        value = _format_value(fact.value_num, fact.unit)
        lines.append(
            f"- {fact.name}: {value}; {_STATUS_LABELS[fact.status]} "
            f"({fact.reference})"
        )
    return "\n".join(lines)


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _num(raw: str) -> float:
    return float(raw.replace(",", "."))


def _as_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _format_reference(ref_low: float | None, ref_high: float | None, ref_text: object) -> str:
    if ref_low is not None and ref_high is not None:
        return f"{ref_low:g}–{ref_high:g}"
    if ref_low is not None:
        return f"от {ref_low:g}"
    if ref_high is not None:
        return f"до {ref_high:g}"
    return _as_text(ref_text) or "не указан"


def _format_value(value_num: float | None, unit: str | None) -> str:
    if value_num is None:
        return "нечисловое значение"
    return f"{value_num:g} {unit or ''}".strip()
