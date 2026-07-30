"""Тесты post-extraction коррекции единиц измерения по справочнику ФСЛИ.

Покрывает:
- очистка пояснений из unit (скобки, слова)
- канонизация надстрочных (10^1  2/л → ×10¹²/л)
- fuzzy-матч неверных единиц (мкМЕ/л → мкМЕ/мл)
- сохранение оригинала в unit_raw
- отсутствие коррекции при отсутствии справочника
"""
from botkin.domain.models import LabResult
from botkin.llm.unit_correction import (
    correct_units as _correct_units,
    strip_unit_explanations as _strip_unit_explanations,
    correct_unit_against_registry as _correct_unit_against_registry,
)


def test_strip_parenthetical_explanation():
    assert _strip_unit_explanations("% (алгоритм ROMA)") == "%"
    assert _strip_unit_explanations("г/л (старый)") == "г/л"
    assert _strip_unit_explanations("КП") == "КП"
    assert _strip_unit_explanations("(+)") == "(+)"


def test_correct_unit_superscript_broken():
    fixed = _correct_unit_against_registry("10^1   2/л", ("10^12/л",))
    assert fixed is not None
    assert fixed == "×10¹²/л"


def test_correct_unit_fuzzy_match():
    fixed = _correct_unit_against_registry("мкМЕ/л", ("мкМЕ/мл",))
    assert fixed is not None
    assert "мл" in fixed


def test_correct_unit_substring_match():
    fixed = _correct_unit_against_registry("Отрицательный КП", ("КП",))
    assert fixed is not None
    assert fixed == "КП"


def test_correct_unit_exact_canonical_match():
    fixed = _correct_unit_against_registry("10^9/л", ("10^9/л",))
    assert fixed is not None


def test_correct_unit_no_match_returns_none():
    fixed = _correct_unit_against_registry("фл", ("пг",))
    assert fixed is None


def test_correct_units_preserves_unit_raw():
    rows = [LabResult(analyte_name="Гемоглобин", value_num=140.0, unit="% (алгоритм ROMA)")]
    _correct_units(rows)
    assert rows[0].unit_raw == "% (алгоритм ROMA)"


def test_correct_units_strips_explanation_without_registry():
    rows = [LabResult(analyte_name="Неизвестный показатель", value_num=1.0, unit="г/л (пояснение)")]
    _correct_units(rows)
    assert rows[0].unit == "г/л"
    assert rows[0].unit_raw == "г/л (пояснение)"


def test_correct_units_skips_empty_unit():
    rows = [LabResult(analyte_name="Гемоглобин", value_num=140.0, unit=None)]
    _correct_units(rows)
    assert rows[0].unit is None


def test_correct_units_does_not_invent_unit():
    rows = [LabResult(analyte_name="Гемоглобин", value_num=140.0, unit="фл")]
    _correct_units(rows)
    assert rows[0].unit == "фл"
