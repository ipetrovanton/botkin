"""Разбор скаляров лабораторного бланка: результат показателя и референсный интервал."""
from __future__ import annotations

import re
from typing import Optional

from botkin.parsing.constants import RANGE_RE, LE_RE, GE_RE, NUM_RE
from botkin.parsing.tokens import to_float


def parse_lab_value(value: object) -> tuple[Optional[float], Optional[str]]:
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
        return to_float(m.group(1)), None
    return None, s


def parse_reference_range(ref: object) -> tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
    """Норма → (ref_low, ref_high, ref_operator, ref_text).

    «35 - 45»→low/high; «< 1.0»→op '<' + high; «> 120»→op '>' + low; «≤/≥»→'<'/'>';
    нечисловая норма→ref_text. Разделённые пробелом тысячи нормализуем: «1 010 - 1 023».
    """
    if ref is None:
        return None, None, None, None
    s = str(ref).strip()
    if not s:
        return None, None, None, None
    # Русские бланки разбивают тысячи пробелом; убираем пробелы между цифрами.
    s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)
    m = RANGE_RE.match(s)
    if m:
        return to_float(m.group(1)), to_float(m.group(2)), None, None
    m = LE_RE.match(s)
    if m:
        return None, to_float(m.group(1)), "<", None
    m = GE_RE.match(s)
    if m:
        return to_float(m.group(1)), None, ">", None
    return None, None, None, s


def looks_like_ref(s: str) -> bool:
    return bool(RANGE_RE.match(s) or LE_RE.match(s) or GE_RE.match(s))


def looks_like_number(s: str) -> bool:
    return bool(re.match(r"^-?\d", s.strip()))


def num_tokens(*values: str) -> list[str]:
    """Нормализованные числовые токены из значений (запятая→точка, без хвостовых .0)."""
    out: list[str] = []
    for v in values:
        if v is None:
            continue
        for m in NUM_RE.findall(str(v)):
            s = m.replace(",", ".")
            if s.endswith(".0"):
                s = s[:-2]
            out.append(s)
    return out
