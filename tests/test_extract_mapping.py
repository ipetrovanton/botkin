"""Маппинг сырого ответа VLM (вложенная схема tests[].results[]) в LabResult.

qwen3-vl возвращает свою естественную структуру с полями parameter/value/
reference_range, а не плоский LabResults. Эти тесты фиксируют контракт адаптера.
"""
from botkin.llm.extract import (
    RawAnalysis,
    harvest_lab_rows,
    parse_lab_value,
    parse_reference_range,
    rows_from_raw,
)


# ── value: число / флаг / запятая / текст / пусто ────────────────────────────

def test_parse_value_plain_number():
    assert parse_lab_value("40.8") == (40.8, None)


def test_parse_value_integer():
    assert parse_lab_value("217") == (217.0, None)


def test_parse_value_with_flag_star():
    # «44.6*» — число с маркером выхода за норму: число берём, текст не ставим
    assert parse_lab_value("44.6*") == (44.6, None)


def test_parse_value_with_trailing_unit():
    # модель иногда вклеивает единицу прямо в значение — берём ведущее число
    assert parse_lab_value("40.8%") == (40.8, None)
    assert parse_lab_value("9 мм/ч") == (9.0, None)
    assert parse_lab_value("13.7 г/дл") == (13.7, None)


def test_parse_value_comma_decimal():
    assert parse_lab_value("5,4") == (5.4, None)


def test_parse_value_textual():
    assert parse_lab_value("не обнаружено") == (None, "не обнаружено")


def test_parse_value_plus():
    assert parse_lab_value("++") == (None, "++")


def test_parse_value_none():
    assert parse_lab_value(None) == (None, None)
    assert parse_lab_value("") == (None, None)


# ── reference_range: двусторонний / односторонний / текст / пусто ────────────

def test_parse_ref_two_sided():
    assert parse_reference_range("35 - 45") == (35.0, 45.0, None, None)


def test_parse_ref_two_sided_decimals():
    assert parse_reference_range("11.7 - 15.5") == (11.7, 15.5, None, None)


def test_parse_ref_less_than():
    assert parse_reference_range("< 1.0") == (None, 1.0, "<", None)


def test_parse_ref_greater_than():
    assert parse_reference_range("> 120") == (120.0, None, ">", None)


def test_parse_ref_unicode_operators():
    assert parse_reference_range("≤ 5.0") == (None, 5.0, "<", None)
    assert parse_reference_range("≥ 10") == (10.0, None, ">", None)


def test_parse_ref_textual():
    assert parse_reference_range("отрицательно") == (None, None, None, "отрицательно")


def test_parse_ref_none():
    assert parse_reference_range(None) == (None, None, None, None)
    assert parse_reference_range("") == (None, None, None, None)


# ── rows_from_raw: уплощение вложенной структуры ─────────────────────────────

def _nested_payload():
    return {
        "patient_id": "881424164",
        "lab": "ИНВИТРО",
        "tests": [
            {
                "test_name": "Клинический анализ крови",
                "results": [
                    {"parameter": "Гематокрит", "value": "40.8", "unit": "%",
                     "reference_range": "35 - 45"},
                    {"parameter": "Нейтрофилы, %", "value": "44.6*", "unit": "%",
                     "reference_range": "48 - 78", "comment": "патологических клеток нет"},
                    {"parameter": "Базофилы, %", "value": "0.6", "unit": "%",
                     "reference_range": "< 1.0"},
                ],
            }
        ],
    }


def test_rows_from_raw_flattens_nested():
    rows = rows_from_raw(RawAnalysis.model_validate(_nested_payload()))
    assert len(rows) == 3
    assert [r.analyte_name for r in rows] == ["Гематокрит", "Нейтрофилы, %", "Базофилы, %"]


def test_rows_from_raw_maps_fields():
    rows = rows_from_raw(RawAnalysis.model_validate(_nested_payload()))
    hct = rows[0]
    assert hct.value_num == 40.8 and hct.value_text is None
    assert hct.unit == "%" and hct.ref_low == 35.0 and hct.ref_high == 45.0
    neu = rows[1]
    assert neu.value_num == 44.6 and neu.value_raw == "44.6*"
    assert neu.comments == "патологических клеток нет"
    bas = rows[2]
    assert bas.ref_operator == "<" and bas.ref_high == 1.0 and bas.ref_low is None


def test_rows_from_raw_accepts_flat_results():
    """Подстраховка: если модель отдаст плоский results[] на верхнем уровне — тоже маппим."""
    flat = {"results": [{"parameter": "Глюкоза", "value": "5,4", "unit": "ммоль/л",
                         "reference_range": "3.9 - 6.1"}]}
    rows = rows_from_raw(RawAnalysis.model_validate(flat))
    assert len(rows) == 1 and rows[0].analyte_name == "Глюкоза" and rows[0].value_num == 5.4


def test_rows_from_raw_skips_rows_without_parameter():
    payload = {"tests": [{"test_name": "X", "results": [
        {"value": "1.0", "unit": "%"},  # нет parameter — пропускаем
        {"parameter": "Гемоглобин", "value": "140", "unit": "г/л"},
    ]}]}
    rows = rows_from_raw(RawAnalysis.model_validate(payload))
    assert len(rows) == 1 and rows[0].analyte_name == "Гемоглобин"


# ── harvester по содержимому: устойчив к ЛЮБЫМ именам ключей и вложенности ────
# qwen3-vl каждый прогон меняет имена ключей (англ/рус) и обёртку — harvester
# классифицирует поля по виду значения и алиасам ключей, имена ключей не фиксированы.

# Реальный ответ модели, прогон №1 (английские ключи, обёртка tests):
_REAL_EN = {
    "patient_id": "881424164",
    "tests": [{"test_name": "Клинический анализ крови", "results": [
        {"parameter": "Гематокрит", "value": "40.8", "unit": "%", "reference_range": "35 - 45"},
        {"parameter": "Нейтрофилы (общ. число), %", "value": "44.6*", "unit": "%",
         "reference_range": "48 - 78", "comment": "патологических клеток не обнаружено"},
        {"parameter": "Базофилы, %", "value": "0.6", "unit": "%", "reference_range": "< 1.0"},
    ]}],
}

# Реальный ответ модели, прогон №2 (русские ключи, пустой верхний ключ):
_REAL_RU = {
    "": {"Исследование": "Клинический анализ крови", "Результат": [
        {"Исследование": "Гематокрит", "Результат": "40.8%", "Единицы": "%", "Референс": "35 - 45"},
        {"Исследование": "Нейтрофилы (общ. число), %", "Результат": "44.6%", "Единицы": "%",
         "Референс": "48 - 78"},
        {"Исследование": "Базофилы, %", "Результат": "0.6%", "Единицы": "%", "Референс": "< 1.0"},
    ]},
}


def test_harvest_english_keys():
    rows = harvest_lab_rows(_REAL_EN)
    assert [r.analyte_name for r in rows] == ["Гематокрит", "Нейтрофилы (общ. число), %", "Базофилы, %"]
    assert rows[0].value_num == 40.8 and rows[0].unit == "%"
    assert rows[0].ref_low == 35.0 and rows[0].ref_high == 45.0
    assert rows[2].ref_operator == "<" and rows[2].ref_high == 1.0


def test_harvest_russian_keys_and_empty_wrapper():
    rows = harvest_lab_rows(_REAL_RU)
    # тот же результат, что и для английских ключей — имена ключей не важны
    assert [r.analyte_name for r in rows] == ["Гематокрит", "Нейтрофилы (общ. число), %", "Базофилы, %"]
    assert rows[0].value_num == 40.8 and rows[0].unit == "%"
    assert rows[0].ref_low == 35.0 and rows[0].ref_high == 45.0
    assert rows[2].ref_operator == "<" and rows[2].ref_high == 1.0


def test_harvest_unit_glued_into_value():
    # «9 мм/ч» с единицей в значении; имя короче единицы — ключ-алиас разрешает неоднозначность
    data = {"data": [{"Показатель": "СОЭ", "Значение": "9 мм/ч", "Норма": "< 20"}]}
    rows = harvest_lab_rows(data)
    assert len(rows) == 1
    assert rows[0].analyte_name == "СОЭ" and rows[0].value_num == 9.0
    assert rows[0].ref_operator == "<" and rows[0].ref_high == 20.0


def test_harvest_textual_value():
    data = {"results": [{"name": "HBsAg", "result": "не обнаружено", "norm": "отрицательно"}]}
    rows = harvest_lab_rows(data)
    assert len(rows) == 1
    assert rows[0].value_text == "не обнаружено" and rows[0].value_num is None
    assert rows[0].ref_text == "отрицательно"


def test_harvest_empty_on_garbage():
    assert harvest_lab_rows({"foo": "bar", "n": 5}) == []
    assert harvest_lab_rows([]) == []
