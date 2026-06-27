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


def _collapse_numeric_spaces(tokens: list[str]) -> list[str]:
    """Склеить соседние числовые токены, разделённые пробелами (1 010 → 1010).

    Русские бланки любят разбивать тысячи пробелом: "1 010 - 1 023".
    Без склеивания парсер видит "010 - 1" и выдаёт ложный референс 10…1.
    """
    if not tokens:
        return tokens
    collapsed = [tokens[0]]
    for tok in tokens[1:]:
        if _PLAIN_NUM_RE.match(collapsed[-1]) and _PLAIN_NUM_RE.match(tok):
            collapsed[-1] = collapsed[-1] + tok
        else:
            collapsed.append(tok)
    return collapsed


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
    rest = _collapse_numeric_spaces(rest)
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


_ANALYZER_PREFIXES = {"cobas", "sysmex", "ves", "roche", "elecsys", "acl", "architect"}


def _is_analyzer_token(prev: str, tok: str) -> bool:
    """True, если токен — номер анализатора (Cobas 6000, Sysmex XN-1000i и т.п.)."""
    if not prev:
        return False
    return prev.rstrip(",;").lower() in _ANALYZER_PREFIXES


def _parse_text_line(line: str) -> Optional[LabResult]:
    """Чистая строка текстового слоя → LabResult, либо None если это не строка-результат.

    Гейт строгий (чтобы не подбирать шапку/подвал — телефон, даты, возраст, обрывки
    примечаний): имя (есть буква) + чистый числовой токен-значение + токен референса.

    Значение ищем только вне скобок: имена вроде «MCH (содержание Hb в 1 Эр.)» содержат
    число внутри пояснения, а настоящее значение — за скобками (sample_009).
    Пропускаем номера анализаторов (Cobas 6000), которые иначе ошибочно становятся значением.
    """
    tokens = line.split()
    paren_depth = 0
    vi = None
    for i, tok in enumerate(tokens):
        paren_depth += tok.count("(") - tok.count(")")
        if paren_depth == 0 and _VALUE_TOKEN_RE.match(tok):
            if not _is_analyzer_token(tokens[i - 1] if i > 0 else "", tok):
                vi = i
                break
    if vi is None or vi == 0:
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


def _value_key(result: LabResult) -> Optional[str]:
    """Нормализованный ключ значения для сравнения покрытия (None → None).

    Предпочитаем value_raw: float не различает 0.6 и 0.60, а на бланке это разные
    показатели (например, базофилы 0.6 % vs моноциты 0.60 ×10^9/л).
    """
    if result.value_raw:
        raw = result.value_raw.strip().replace(",", ".")
        if raw.endswith("*"):
            raw = raw[:-1]
        return raw
    toks = num_tokens(result.value_num)
    return toks[0] if toks else None


def completeness_guard(lines: list[str], rows: list[LabResult]) -> list[LabResult]:
    """Строки-результаты источника, не представленные в rows → восстановленные LabResult.

    Покрытие считаем ПО ЗНАЧЕНИЮ показателя, а не по имени: имена от LLM и от парсера
    строки могут расходиться, а значение — стабильный якорь. Так не плодим ложные дубли.
    Недобор при коллизии значений безопаснее ложного добора — мы лишь не хуже прежнего.
    Симметрично verbatim_guard: тот ловит лишнее, этот — пропущенное (одинокий результат
    на отдельной странице LLM иногда теряет).
    """
    covered = {_value_key(r) for r in rows}
    covered.discard(None)
    recovered: list[LabResult] = []
    for line in lines:
        r = _parse_text_line(line)
        if r is None:
            continue
        key = _value_key(r)
        if key is None or key in covered:
            continue
        covered.add(key)
        recovered.append(r)
    return recovered
