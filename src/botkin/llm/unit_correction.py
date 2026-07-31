"""Пост-экстракционная коррекция единиц измерения по справочнику ФСЛИ.

Исправляет OCR-артефакты в единицах (надстрочные, пояснения, неверные единицы),
используя реестр ФСЛИ и канонизацию через UNIT_ALIASES.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

from botkin.domain.models import LabResult
from botkin.normalize.analytes import AnalyteNormalizer, load_default as _load_analyte_normalizer
from botkin.normalize.units import canonical_unit

log = logging.getLogger(__name__)

_UNIT_PARENS_RE = re.compile(r"\([^)]*\)")
_FUZZY_UNIT_THRESHOLD = 85


@lru_cache(maxsize=1)
def get_normalizer() -> AnalyteNormalizer:
    """Синглтон AnalyteNormalizer — справочник ФСЛИ читается один раз."""
    return _load_analyte_normalizer()


def strip_unit_explanations(unit: str) -> str:
    """Убирает пояснения из unit: '% (алгоритм ROMA)' → '%'."""
    cleaned = _UNIT_PARENS_RE.sub("", unit).strip()
    return cleaned if cleaned else unit


def correct_unit_against_registry(
    unit: str, expected_units: tuple[str, ...],
) -> str | None:
    """Если unit не совпадает с ожидаемыми из ФСЛИ, пытается исправить.

    Возвращает каноничную единицу или None, если исправить нельзя.
    """
    if not unit or not expected_units:
        return None

    canon, _ = canonical_unit(unit)
    expected_canon = {canonical_unit(u)[0]: u for u in expected_units}

    if canon in expected_canon:
        return canon

    stripped = strip_unit_explanations(unit)
    if stripped != unit:
        canon_stripped, _ = canonical_unit(stripped)
        if canon_stripped in expected_canon:
            return canon_stripped

    unit_lower = unit.lower().strip()
    for exp in expected_units:
        exp_lower = exp.lower().strip()
        if len(exp_lower) >= 2 and exp_lower in unit_lower:
            return canonical_unit(exp)[0]

    from rapidfuzz import fuzz
    best_score = 0
    best_canon = None
    for exp in expected_units:
        exp_canon = canonical_unit(exp)[0]
        score = fuzz.ratio(canon.lower(), exp_canon.lower())
        if score > best_score:
            best_score = score
            best_canon = exp_canon

    if best_score >= _FUZZY_UNIT_THRESHOLD:
        return best_canon

    return None


def correct_units(rows: list[LabResult]) -> list[LabResult]:
    """Пост-экстракционная коррекция единиц по справочнику ФСЛИ.

    Исправляет:
    - Надстрочные индексы, разбитые OCR: 10^1  2/л → ×10¹²/л
    - Пояснения в unit: '% (алгоритм ROMA)' → '%'
    - Неверные единицы: мкМЕ/л → мкМЕ/мл (если в справочнике мкМЕ/мл)

    Оригинал сохраняется в unit_raw для аудита.
    """
    normalizer = get_normalizer()
    corrected_count = 0
    for row in rows:
        if not row.unit:
            continue
        if not row.unit_raw:
            row.unit_raw = row.unit

        match = normalizer.correct(row.analyte_name)
        if match.status == "matched" and match.expected_units:
            fixed = correct_unit_against_registry(row.unit, match.expected_units)
            if fixed and fixed != row.unit:
                row.unit = fixed
                corrected_count += 1
            elif fixed:
                row.unit = fixed
        else:
            cleaned = strip_unit_explanations(row.unit)
            if cleaned != row.unit:
                row.unit = cleaned
                corrected_count += 1

    if corrected_count:
        log.info("[UNIT_CORRECTION] Исправлено единиц: %d/%d", corrected_count, len(rows))
    return rows
