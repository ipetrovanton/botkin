"""Анти-пропускной страж текстового слоя: добор строк-результатов, что выпали из LLM.

Симметрия к verbatim_guard (анти-галлюцинация). Гейт строки-результата:
имя + чистый числовой токен-значение + токен референса («<x» / «a - b»).
Покрытие считаем ПО ЗНАЧЕНИЮ (не по имени) — это исключает ложные дубли.
"""
from botkin.domain.models import LabResult
from botkin.llm.extract import _parse_text_line, completeness_guard


# ── _parse_text_line: парсинг чистой строки текстового слоя ──────────────────

def test_parse_lone_result_with_le_reference():
    r = _parse_text_line("С-реактивный белок 1.8 мг/л <5.0")
    assert r is not None
    assert r.analyte_name == "С-реактивный белок"
    assert r.value_num == 1.8
    assert r.unit == "мг/л"
    assert r.ref_high == 5.0
    assert r.ref_operator == "<"


def test_parse_result_with_range_reference():
    r = _parse_text_line("Гематокрит 40.8 % 35 - 45")
    assert r is not None
    assert r.analyte_name == "Гематокрит"
    assert r.value_num == 40.8
    assert r.unit == "%"
    assert r.ref_low == 35.0 and r.ref_high == 45.0


def test_parse_strips_value_flag_keeps_operator_reference():
    r = _parse_text_line("Лимфоциты, % 40* % 19 - 37")
    assert r is not None
    assert r.value_num == 40.0  # флаг «*» отброшен
    assert r.unit == "%"
    assert r.ref_low == 19.0 and r.ref_high == 37.0


def test_parse_rejects_patient_line_without_reference():
    # «Возраст: 34 года» — есть число, но нет референс-паттерна → не результат.
    assert _parse_text_line("Возраст: 34 года") is None


def test_parse_rejects_phone_line():
    # Телефон 8-800-… — нет чистого числового токена-значения с референсом.
    assert _parse_text_line('Пол: Жен 8-800-200-363-0') is None


def test_parse_rejects_comment_fragment_with_glued_percent():
    # «превышает 6%» — значение склеено с единицей, нет референса → не результат.
    assert _parse_text_line("превышает 6%") is None


def test_parse_rejects_date_line():
    assert _parse_text_line("Дата рождения: 25.11.1991") is None


# ── completeness_guard: добор только реально пропущенных строк ────────────────

def test_recovers_missing_lone_result():
    # LLM вернул только ОАК — СРБ со стр.1 пропущен, страж его добирает.
    lines = [
        "С-реактивный белок 1.8 мг/л <5.0",
        "Гемоглобин 13.7 г/дл 11.7 - 15.5",
    ]
    rows = [LabResult(analyte_name="Гемоглобин", value_num=13.7, value_raw="13.7",
                      ref_low=11.7, ref_high=15.5)]
    recovered = completeness_guard(lines, rows)
    assert [r.analyte_name for r in recovered] == ["С-реактивный белок"]
    assert recovered[0].value_num == 1.8


def test_recovers_nothing_when_all_covered():
    lines = ["Гемоглобин 13.7 г/дл 11.7 - 15.5"]
    rows = [LabResult(analyte_name="Гемоглобин", value_num=13.7, value_raw="13.7")]
    assert completeness_guard(lines, rows) == []


def test_ignores_non_result_lines():
    lines = [
        "САУЛИНА ИННА ИГОРЕВНА",
        "Возраст: 34 года",
        "Дата печати результата: 28.03.2026",
        "превышает 6%",
    ]
    assert completeness_guard(lines, []) == []
