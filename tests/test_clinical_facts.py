"""Проверки детерминированного слоя лабораторных фактов."""
import pytest

from botkin.clinical.facts import (
    build_lab_facts,
    classify_value,
    parse_reference_range,
    render_lab_facts,
)


@pytest.mark.parametrize(
    ("value", "low", "high", "expected"),
    [
        (119, 120, 150, "low"),
        (151, 120, 150, "high"),
        (135, 120, 150, "normal"),
        (5, None, None, "unknown"),
        (5, None, 4, "high"),
        (5, 6, None, "low"),
    ],
)
def test_classify_value_uses_only_reference_bounds(value, low, high, expected):
    assert classify_value(value, low, high) == expected


def test_build_lab_facts_accepts_database_rows():
    facts = build_lab_facts([
        {
            "name": "Гемоглобин",
            "value_num": 92,
            "unit": "г/л",
            "ref_low": 120,
            "ref_high": 150,
        },
    ])
    assert facts[0].name == "Гемоглобин"
    assert facts[0].status == "low"
    assert facts[0].reference == "120–150"


def test_build_lab_facts_skips_unnamed_rows():
    assert build_lab_facts([{"value_num": 10, "ref_high": 5}]) == []


def test_render_lab_facts_is_explicit_about_scope():
    facts = build_lab_facts([
        {"name": "TSH", "value_num": 6.8, "unit": "мкМЕ/мл", "ref_low": 0.4, "ref_high": 4},
    ])
    rendered = render_lab_facts(facts)
    assert "только сравнение с референсом" in rendered
    assert "TSH: 6.8 мкМЕ/мл; выше референса (0.4–4)" in rendered


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("220 - 450 umol/L", (220.0, 450.0)),
        ("0.8 - 1.2 mg/dL", (0.8, 1.2)),
        ("0 - 5 mg/L", (0.0, 5.0)),
        ("60 - 80 g/L", (60.0, 80.0)),
        ("1.8 - 5.0 mmol/L", (1.8, 5.0)),
        ("220–450", (220.0, 450.0)),  # en-dash без единиц
        ("0,4 - 4,0", (0.4, 4.0)),  # десятичная запятая
        ("< 5", (None, 5.0)),
        ("≤ 5 мг/л", (None, 5.0)),
        ("> 10", (10.0, None)),
        ("до 450", (None, None)),  # словесная форма не поддерживается
        ("отрицательно", (None, None)),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_parse_reference_range(text, expected):
    assert parse_reference_range(text) == expected


@pytest.mark.parametrize(
    ("name", "value", "ref_text", "expected_status"),
    [
        ("Мочевая кислота", 581.09, "220 - 450 umol/L", "high"),
        ("Creatinine", 1.781, "0.8 - 1.2 mg/dL", "high"),
        ("BUN", 10.04, "1.8 - 5.0 mmol/L", "high"),
        ("Total Protein", 99.0, "60 - 80 g/L", "high"),
        ("CRP", 3.06, "0 - 5 mg/L", "normal"),
    ],
)
def test_build_lab_facts_parses_numeric_reference_from_text(
    name, value, ref_text, expected_status
):
    """Регрессия: числовой референс из ref_text даёт рабочий флаг нормы.

    Ранее эти показатели помечались 'unknown'/'в норме', потому что числовые
    ref_low/ref_high были NULL, а диапазон лежал только в текстовом поле.
    """
    facts = build_lab_facts([
        {"name": name, "value_num": value, "unit": "", "ref_text": ref_text},
    ])
    assert facts[0].status == expected_status


def test_build_lab_facts_keeps_original_reference_text():
    """Оригинальный текст референса сохраняется для отображения, флаг считается по числам."""
    facts = build_lab_facts([
        {"name": "Мочевая кислота", "value_num": 581.09, "ref_text": "220 - 450 umol/L"},
    ])
    assert facts[0].status == "high"
    assert facts[0].reference == "220 - 450 umol/L"
