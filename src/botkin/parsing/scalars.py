"""Разбор скаляров лабораторного бланка: результат показателя и референсный интервал."""
from __future__ import annotations

import re
from typing import Optional

_RANGE_RE = re.compile(r"^(-?\d+(?:[.,]\d+)?)\s*[-–—]\s*(-?\d+(?:[.,]\d+)?)$")
_LE_RE = re.compile(r"^[<≤]\s*(-?\d+(?:[.,]\d+)?)$")
_GE_RE = re.compile(r"^[>≥]\s*(-?\d+(?:[.,]\d+)?)$")
_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


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


def looks_like_ref(s: str) -> bool:
    return bool(_RANGE_RE.match(s) or _LE_RE.match(s) or _GE_RE.match(s))


def looks_like_number(s: str) -> bool:
    return bool(re.match(r"^-?\d", s.strip()))


def num_tokens(*values) -> list[str]:
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
