"""Разбор строк текстового слоя PDF и два стража качества (verbatim/completeness)."""
from __future__ import annotations

import re
from typing import Optional

from botkin.domain.models import LabResult
from botkin.parsing.scalars import (
    _GE_RE, _LE_RE, _RANGE_RE, num_tokens, parse_lab_value, parse_reference_range,
)

_VALUE_TOKEN_RE = re.compile(r"^-?\d+(?:[.,]\d+)?\*?$")  # чистый токен-значение (+флаг «*»)
_PLAIN_NUM_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")
_OPERATORS = ("<", ">", "≤", "≥")
_DASHES = ("-", "–", "—")


def _verbatim_guard(rows: list[LabResult], src_text: str):
    """Делит строки на (kept, rejected): каждое число строки обязано быть в src_text.

    Числа источника собираем в множество нормализованных токенов; строка проходит,
    если ВСЕ её числа (value_raw + границы референса) присутствуют в источнике.
    Ловит галлюцинации модели — число, которого в документе нет.
    """
    source_nums = set(num_tokens(src_text))
    kept: list[LabResult] = []
    rejected: list[LabResult] = []
    for r in rows:
        tokens = num_tokens(r.value_raw, r.ref_low, r.ref_high, r.ref_text)
        if all(t in source_nums for t in tokens):
            kept.append(r)
        else:
            rejected.append(r)
    return kept, rejected


def _extract_unit_ref(rest: list[str]) -> tuple[Optional[str], Optional[str]]:
    """Хвост строки после значения → (unit, reference). Нет референса → (unit, None)."""
    for i, tok in enumerate(rest):
        # одно-токенные формы: «<5.0», «<20», «35-45»
        if _LE_RE.match(tok) or _GE_RE.match(tok) or _RANGE_RE.match(tok):
            return (" ".join(rest[:i]) or None), tok
        # «< 1.0» — оператор + число отдельными токенами
        if tok in _OPERATORS and i + 1 < len(rest) and re.match(r"^-?\d", rest[i + 1]):
            return (" ".join(rest[:i]) or None), tok + rest[i + 1]
        # «35 - 45» — число, тире, число
        if (_PLAIN_NUM_RE.match(tok) and i + 2 < len(rest)
                and rest[i + 1] in _DASHES and _PLAIN_NUM_RE.match(rest[i + 2])):
            return (" ".join(rest[:i]) or None), f"{tok} - {rest[i + 2]}"
    return (" ".join(rest) or None), None


def _parse_text_line(line: str) -> Optional[LabResult]:
    """Чистая строка текстового слоя → LabResult, либо None если это не строка-результат.

    Гейт строгий (чтобы не подбирать шапку/подвал — телефон, даты, возраст, обрывки
    примечаний): имя (есть буква) + чистый числовой токен-значение + токен референса.
    """
    tokens = line.split()
    vi = next((i for i, t in enumerate(tokens) if _VALUE_TOKEN_RE.match(t)), None)
    if not vi:  # нет токена-значения, либо значение в самом начале (нет имени)
        return None
    name = " ".join(tokens[:vi]).strip()
    if not any(ch.isalpha() for ch in name):
        return None
    unit, ref = _extract_unit_ref(tokens[vi + 1:])
    if ref is None:  # без референса не считаем строкой-результатом
        return None
    value_num, value_text = parse_lab_value(tokens[vi])
    ref_low, ref_high, ref_operator, ref_text = parse_reference_range(ref)
    return LabResult(
        analyte_name=name, value_num=value_num, value_text=value_text,
        value_raw=tokens[vi], unit=unit,
        ref_low=ref_low, ref_high=ref_high, ref_operator=ref_operator, ref_text=ref_text,
    )


def _value_key(value) -> Optional[str]:
    """Нормализованный токен значения для сравнения покрытия (None → None)."""
    toks = num_tokens(value)
    return toks[0] if toks else None


def completeness_guard(lines: list[str], rows: list[LabResult]) -> list[LabResult]:
    """Строки-результаты источника, не представленные в rows → восстановленные LabResult.

    Покрытие считаем ПО ЗНАЧЕНИЮ показателя, а не по имени: имена от LLM и от парсера
    строки могут расходиться, а значение — стабильный якорь. Так не плодим ложные дубли.
    Недобор при коллизии значений безопаснее ложного добора — мы лишь не хуже прежнего.
    Симметрично verbatim_guard: тот ловит лишнее, этот — пропущенное (одинокий результат
    на отдельной странице LLM иногда теряет).
    """
    covered = {_value_key(r.value_num) for r in rows}
    covered.discard(None)
    recovered: list[LabResult] = []
    for line in lines:
        r = _parse_text_line(line)
        if r is None:
            continue
        key = _value_key(r.value_num)
        if key is None or key in covered:
            continue
        covered.add(key)
        recovered.append(r)
    return recovered
