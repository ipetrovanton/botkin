"""Разбор строк текстового слоя PDF и два стража качества (verbatim/completeness)."""
from __future__ import annotations

import re
from typing import Optional

from botkin.domain.models import LabResult
from botkin.parsing.constants import RANGE_RE, LE_RE, GE_RE
from botkin.parsing.scalars import (
    num_tokens, parse_lab_value, parse_reference_range,
)

_VALUE_TOKEN_RE = re.compile(r"^-?\d+(?:[.,]\d+)?\*?$")  # чистый токен-значение (+флаг «*»)
_PLAIN_NUM_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")
_INT_ONLY_RE = re.compile(r"^-?\d+$")  # целое без десятичной части — для склейки тысяч
_OPERATORS = ("<", ">", "≤", "≥")
_DASHES = ("-", "–", "—")


def _collapse_numeric_spaces(tokens: list[str]) -> list[str]:
    """Склеить соседние целочисленные токены, разделённые пробелами (1 010 → 1010).

    Русские бланки любят разбивать тысячи пробелом: "1 010 - 1 023".
    Без склеивания парсер видит "010 - 1" и выдаёт ложный референс 10…1.
    Склейка применяется ТОЛЬКО к целым числам без десятичной точки/запятой —
    иначе "5.5 5" (два значения) склеится в "5.55" (одно число).
    """
    if not tokens:
        return tokens
    collapsed = [tokens[0]]
    for tok in tokens[1:]:
        if _INT_ONLY_RE.match(collapsed[-1]) and _INT_ONLY_RE.match(tok):
            collapsed[-1] = collapsed[-1] + tok
        else:
            collapsed.append(tok)
    return collapsed


def _verbatim_guard(rows: list[LabResult], src_text: str) -> tuple[list[LabResult], list[LabResult]]:
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


def _extract_unit_ref(rest: list[str]) -> tuple[Optional[str], Optional[str], int]:
    """Хвост строки после значения → (unit, reference, consumed_tokens). Нет референса → (unit, None, len).

    Ожидает уже свёрнутый список (см. _collapse_numeric_spaces): consumed_tokens —
    индекс в этом же списке, по которому можно отрезать хвост и искать следующий
    показатель на той же физической строке (multi-result парсинг).
    """
    for i, tok in enumerate(rest):
        # одно-токенные формы: «<5.0», «<20», «35-45»
        if LE_RE.match(tok) or GE_RE.match(tok) or RANGE_RE.match(tok):
            return (" ".join(rest[:i]) or None), tok, i + 1
        # «< 1.0» — оператор + число отдельными токенами
        if tok in _OPERATORS and i + 1 < len(rest) and re.match(r"^-?\d", rest[i + 1]):
            return (" ".join(rest[:i]) or None), tok + rest[i + 1], i + 2
        # «35 - 45» — число, тире, число
        if (_PLAIN_NUM_RE.match(tok) and i + 2 < len(rest)
                and rest[i + 1] in _DASHES and _PLAIN_NUM_RE.match(rest[i + 2])):
            return (" ".join(rest[:i]) or None), f"{tok} - {rest[i + 2]}", i + 3
    return (" ".join(rest) or None), None, len(rest)


_ANALYZER_PREFIXES = {"cobas", "sysmex", "ves", "roche", "elecsys", "acl", "architect"}


def _is_analyzer_token(prev: str, tok: str) -> bool:
    """True, если токен — номер анализатора (Cobas 6000, Sysmex XN-1000i и т.п.)."""
    if not prev:
        return False
    return prev.rstrip(",;").lower() in _ANALYZER_PREFIXES


def _is_name_embedded_number(tokens: list[str], i: int) -> bool:
    """True, если число tokens[i] — часть имени аналита (Ca 125, HE4), а не значение.

    Признак: сразу за числом идёт ЕЩЁ одно число (настоящее значение), а за ним —
    единица/оператор, а НЕ тире-диапазон и не число. Так «Са 125 8.13 Ед/мл < 35»
    (значение 8.13) отличается от «pH 5.5 5 - 8» (значение 5.5, «5 - 8» — референс)
    и от разбитых пробелом тысяч «1 010 150» (за числом снова число).
    """
    if i + 2 >= len(tokens):
        return False
    nxt, after = tokens[i + 1], tokens[i + 2]
    if not _PLAIN_NUM_RE.match(nxt):
        return False
    return after not in _DASHES and not _PLAIN_NUM_RE.match(after)


def _parse_first_result(tokens: list[str]) -> tuple[Optional[LabResult], list[str]]:
    """Первый LabResult из токенов + остаток токенов для поиска следующего показателя.

    (None, []) — строка-результат не найдена. Гейт строгий (чтобы не подбирать шапку/подвал
    — телефон, даты, возраст, обрывки примечаний): имя (есть буква) + чистый числовой
    токен-значение + токен референса.

    Значение ищем только вне скобок: имена вроде «MCH (содержание Hb в 1 Эр.)» содержат
    число внутри пояснения, а настоящее значение — за скобками (sample_009).
    Пропускаем номера анализаторов (Cobas 6000), которые иначе ошибочно становятся значением.
    """
    paren_depth = 0
    vi = None
    for i, tok in enumerate(tokens):
        paren_depth += tok.count("(") - tok.count(")")
        if paren_depth == 0 and _VALUE_TOKEN_RE.match(tok):
            prev = tokens[i - 1] if i > 0 else ""
            if not _is_analyzer_token(prev, tok) and not _is_name_embedded_number(tokens, i):
                vi = i
                break
    if vi is None or vi == 0:
        return None, []
    name = " ".join(tokens[:vi]).strip()
    if not any(ch.isalpha() for ch in name):
        return None, []
    # Свёртываем хвост после значения один раз; consumed — индекс в этом же списке,
    # по нему отрезаем остаток для поиска следующего показателя на той же строке.
    tail = _collapse_numeric_spaces(tokens[vi + 1:])
    unit, ref, consumed = _extract_unit_ref(tail)
    if ref is None:  # без референса не считаем строкой-результатом
        return None, []
    value_num, value_text = parse_lab_value(tokens[vi])
    ref_low, ref_high, ref_operator, ref_text = parse_reference_range(ref)
    result = LabResult(
        analyte_name=name, value_num=value_num, value_text=value_text,
        value_raw=tokens[vi], unit=unit,
        ref_low=ref_low, ref_high=ref_high, ref_operator=ref_operator, ref_text=ref_text,
    )
    return result, tail[consumed:]


def _parse_text_line(line: str) -> Optional[LabResult]:
    """Чистая строка текстового слоя → первый LabResult, либо None если это не результат."""
    result, _ = _parse_first_result(line.split())
    return result


def _parse_text_line_all(line: str) -> list[LabResult]:
    """Все LabResult из одной строки — бланк мог склеить >1 показателя в физическую строку.

    Пример: «Относительная плотность 1017 г/л 1003 - 1035 pH 5.5 5 - 8» — два показателя
    (плотность и pH) на одной строке. Проходим по строке, отрезая обработанные токены
    после каждого результата.
    """
    results: list[LabResult] = []
    tokens = line.split()
    while tokens:
        result, tokens = _parse_first_result(tokens)
        if result is None:
            break
        results.append(result)
    return results


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

    Использует _parse_text_line_all вместо _parse_text_line: бланк мог склеить >1
    показателя в одну физическую строку (пример: «Относительная плотность 1017 г/л
    1003 - 1035 pH 5.5 5 - 8»). Без multi-result парсинга второй показатель теряется.
    """
    covered = {_value_key(r) for r in rows}
    covered.discard(None)
    recovered: list[LabResult] = []
    for line in lines:
        for r in _parse_text_line_all(line):
            key = _value_key(r)
            if key is None or key in covered:
                continue
            covered.add(key)
            recovered.append(r)
    return recovered
