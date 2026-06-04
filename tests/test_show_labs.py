from botkin.bot.handlers.show import _format_labs, _format_ref


def _row(**kw):
    base = dict(analyte_name="X", value_num=None, value_text=None, unit=None,
                ref_low=None, ref_high=None, ref_operator=None, ref_text=None,
                analyte_canonical=None, loinc=None, nmu_code=None, analyte_group=None,
                match_status=None, unit_expected=None, unit_mismatch=None)
    base.update(kw)
    return base


def test_text_result_rendered_not_none():
    out = _format_labs([_row(analyte_name="Антитела", value_text="не обнаружено")])
    assert "не обнаружено" in out
    assert "None" not in out


def test_one_sided_ref_shown():
    out = _format_labs([_row(analyte_name="СРБ", value_num=1.8, unit="мг/л",
                             ref_operator="<", ref_high=5.0)])
    assert "1.8" in out and "&lt;5.0" in out


def test_ref_operator_html_escaped():
    # «< 1.0» (базофилы) ломал Telegram parse_mode=HTML: '<1.0' читался как тег.
    # Вывод не должен содержать сырой '<' от оператора нормы — только &lt;.
    out = _format_labs([_row(analyte_name="Базофилы", value_num=0.6, unit="%",
                             ref_operator="<", ref_high=1.0)])
    assert "&lt;1.0" in out
    assert "<1.0" not in out


def test_two_sided_ref_and_high_marker():
    out = _format_labs([_row(analyte_name="Глюкоза", value_num=7.0, unit="ммоль/л",
                             ref_low=3.9, ref_high=6.1)])
    assert "3.9" in out and "6.1" in out and "⬆️" in out


def test_low_marker_with_operator_ref():
    # value ниже нижней границы ">120"
    out = _format_labs([_row(analyte_name="X", value_num=100.0,
                             ref_operator=">", ref_low=120.0)])
    assert "⬇️" in out


def test_text_ref_shown():
    out = _format_labs([_row(analyte_name="HBsAg", value_text="отрицательно",
                             ref_text="отрицательно")])
    assert "отрицательно" in out


def test_unit_mismatch_warning():
    out = _format_labs([_row(analyte_name="Глюкоза", value_num=5.4, unit="г/л",
                             unit_expected="ммоль/л", unit_mismatch=1)])
    assert "⚠️" in out


def test_empty_rows():
    assert _format_labs([]) == "—"


def test_format_ref_helper():
    assert _format_ref(_row(ref_low=3.9, ref_high=6.1)) == "норма 3.9–6.1"
    assert _format_ref(_row(ref_operator="<", ref_high=5.0)) == "норма <5.0"
    assert _format_ref(_row(ref_operator=">", ref_low=120.0)) == "норма >120.0"
    assert _format_ref(_row(ref_text="отрицательно")) == "норма: отрицательно"
    assert _format_ref(_row()) == ""
