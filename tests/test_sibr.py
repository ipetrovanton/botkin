from botkin.domain.models import LabResult
from botkin.parsing.sibr import is_sibr_text, parse_sibr_ocr


def test_is_sibr_text_detects_sibr():
    assert is_sibr_text("Водородно-метановый дыхательный тест (СИБР) с лактулозой")
    assert not is_sibr_text("Общий анализ крови")


def test_is_sibr_text_detects_gas_column_headers():
    # OCR может вернуть только колонки без слова «СИБР».
    assert is_sibr_text("H2 ppm CH4 ppm H2+2CH4 ppm O2 %")
    assert not is_sibr_text("CO2 O2 N2")  # отсутствует CH4 — не СИБР


def test_parse_sibr_ocr_returns_rows_for_all_gases():
    text = "0 мин: H2=7 ppm, CH4=13 ppm, H2+2CH4=33 ppm, O2=17 %\n" \
           "15 мин: H2=11 ppm, CH4=14 ppm, H2+2CH4=39 ppm, O2=17 %"
    rows = parse_sibr_ocr(text)
    assert len(rows) == 8
    assert rows[0] == LabResult(
        analyte_name="СИБР-тест: 0 минут, водород H2",
        value_num=7.0,
        value_raw="7",
        unit="ppm",
    )
    assert rows[-1] == LabResult(
        analyte_name="СИБР-тест: 15 минут, кислород O2",
        value_num=17.0,
        value_raw="17",
        unit="% КВМ",
    )


def test_parse_sibr_ocr_strips_result_asterisk():
    text = "75 мин: H2=113* ppm, CH4=22 ppm, H2+2CH4=157 ppm, O2=17 %"
    rows = parse_sibr_ocr(text)
    h2 = next(r for r in rows if "водород H2" in r.analyte_name)
    assert h2.value_num == 113.0
    assert h2.value_raw == "113"


def test_parse_sibr_ocr_handles_compact_format():
    # Регрессия: модель может вернуть компактный формат без единиц и с прилипшим %.
    text = "0 мин: H2=7, CH4=13, H2+2CH4=33, O2=17%\n" \
           "120 мин: H2=95, CH4=24, H2+2CH4=143, O2=18%"
    rows = parse_sibr_ocr(text)
    assert len(rows) == 8
    h2 = next(r for r in rows if r.analyte_name == "СИБР-тест: 120 минут, водород H2")
    assert h2.value_num == 95.0
    o2 = next(r for r in rows if r.analyte_name == "СИБР-тест: 120 минут, кислород O2")
    assert o2.value_num == 18.0
    assert o2.unit == "% КВМ"
