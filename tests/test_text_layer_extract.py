"""Структурирование текстового слоя и гейт текстового слоя в run_analysis."""
from pathlib import Path

import botkin.llm.extract as ex
from botkin.llm import text_extract as te
from botkin.llm.extract import RawAnalysis
from botkin.domain.models import LabResult
from botkin.preprocess.pdf_text import PdfTextData


def test_structure_text_maps_raw_to_rows(monkeypatch):
    # Модель размечает строки в RawAnalysis; маппинг → LabResult идёт через rows_from_raw.
    raw = RawAnalysis.model_validate({"results": [
        {"parameter": "Гемоглобин", "value": "13.7", "unit": "г/дл",
         "reference_range": "11.7 - 15.5"},
        {"parameter": "Эритроциты", "value": "4.64", "unit": "млн/мкл",
         "reference_range": "3.8 - 5.1"},
    ]})
    monkeypatch.setattr(te, "TEXT_COMPACT_OUTPUT", False)  # тест про JSON-путь, не про компакт
    monkeypatch.setattr(te, "call_text", lambda messages, name, structured=None: raw)
    rows = te.structure_text(["Гемоглобин 13.7 г/дл 11.7 - 15.5",
                               "Эритроциты 4.64 млн/мкл 3.8 - 5.1"], "doc.pdf")
    names = [r.analyte_name for r in rows]
    assert names == ["Гемоглобин", "Эритроциты"]
    assert rows[0].unit == "г/дл" and rows[0].value_num == 13.7


def test_structure_text_retries_without_grammar_on_empty(monkeypatch):
    """XGrammar иногда возвращает пустой объект: structured пуст → повтор без grammar.

    Регрессия sample_001 (онкомаркеры): без ретрая текстовый слой флапал ~50% прогонов,
    пустой ответ подменялся мусором от completeness_guard. Ретрай стабилизировал до 6/6.
    """
    empty = RawAnalysis.model_validate({"results": []})
    filled = RawAnalysis.model_validate({"results": [
        {"parameter": "Гемоглобин", "value": "13.7", "unit": "г/дл", "reference_range": "11.7 - 15.5"},
    ]})
    calls = []

    def fake_call_text(messages, name, structured=None):
        calls.append(structured)
        # Первый (structured) вызов пуст, повтор без grammar — с данными.
        return empty if structured is None else filled

    monkeypatch.setattr(te, "TEXT_COMPACT_OUTPUT", False)  # тест про JSON-путь, не про компакт
    monkeypatch.setattr(te, "call_text", fake_call_text)
    rows = te.structure_text(["Гемоглобин 13.7 г/дл 11.7 - 15.5"], "doc.pdf")

    assert calls == [None, False]  # structured, затем unstructured-повтор
    assert [r.analyte_name for r in rows] == ["Гемоглобин"]


def test_structure_text_stops_retrying_after_limit(monkeypatch):
    """Пустой ответ и без grammar → ограничиваем повторы _TEXT_EMPTY_RETRIES, не зациклимся."""
    empty = RawAnalysis.model_validate({"results": []})
    calls = []

    def fake_call_text(messages, name, structured=None):
        calls.append(structured)
        return empty

    monkeypatch.setattr(te, "TEXT_COMPACT_OUTPUT", False)  # тест про JSON-путь, не про компакт
    monkeypatch.setattr(te, "call_text", fake_call_text)
    rows = te.structure_text(["мусор без показателей"], "doc.pdf")

    assert rows == []
    # 1 structured + _TEXT_EMPTY_RETRIES unstructured-попыток.
    assert calls == [None] + [False] * te._TEXT_EMPTY_RETRIES


def test_structure_text_uses_compact_and_skips_json_call(monkeypatch):
    """Компактный путь основной: при непустом разборе JSON-схему не дёргаем вовсе."""
    monkeypatch.setattr(te, "TEXT_COMPACT_OUTPUT", True)
    monkeypatch.setattr(te, "call_text_compact",
                        lambda messages, name: [LabResult(analyte_name="Гемоглобин", value_num=13.7)])

    def must_not_be_called(*a, **kw):
        raise AssertionError("JSON-схема не должна вызываться, если компакт дал строки")

    monkeypatch.setattr(te, "call_text", must_not_be_called)
    rows = te.structure_text(["Гемоглобин 13.7 г/дл"], "doc.pdf")
    assert [r.analyte_name for r in rows] == ["Гемоглобин"]


def test_structure_text_falls_back_to_json_when_compact_empty(monkeypatch):
    """Пустой компакт не должен терять страницу — откат на прежний JSON-путь."""
    filled = RawAnalysis.model_validate({"results": [{"parameter": "СРБ", "value": "1.0"}]})
    monkeypatch.setattr(te, "TEXT_COMPACT_OUTPUT", True)
    monkeypatch.setattr(te, "call_text_compact", lambda messages, name: [])
    monkeypatch.setattr(te, "call_text", lambda messages, name, structured=None: filled)
    rows = te.structure_text(["СРБ 1.0"], "doc.pdf")
    assert [r.analyte_name for r in rows] == ["СРБ"]


def test_structure_text_falls_back_to_json_when_compact_raises(monkeypatch):
    """Сбой сервера на компактном вызове тоже не должен терять страницу."""
    filled = RawAnalysis.model_validate({"results": [{"parameter": "СРБ", "value": "1.0"}]})

    def boom(messages, name):
        raise RuntimeError("500 server error")

    monkeypatch.setattr(te, "TEXT_COMPACT_OUTPUT", True)
    monkeypatch.setattr(te, "call_text_compact", boom)
    monkeypatch.setattr(te, "call_text", lambda messages, name, structured=None: filled)
    rows = te.structure_text(["СРБ 1.0"], "doc.pdf")
    assert [r.analyte_name for r in rows] == ["СРБ"]


def _make_pdf_data(pages: list[list[str]]) -> PdfTextData:
    flat = "\n".join(ln for pg in pages for ln in pg)
    return PdfTextData(pages=pages, flat_text=flat)


def test_text_layer_extracts_each_page_so_lone_result_survives(monkeypatch):
    # Регресс doc#28: одинокий результат на стр.1 (С-реактивный белок) терялся при
    # едином вызове по всем страницам. Постранично — модель видит каждую страницу.
    pages = [["С-реактивный белок 1.8 мг/л <5.0"],
             ["Гемоглобин 13.7 г/дл 11.7 - 15.5"]]
    monkeypatch.setattr(te, "open_pdf", lambda p, **kw: _make_pdf_data(pages))

    def fake_structure(lines, name):
        # Имитация фокуса модели: видит ТОЛЬКО строки переданной страницы.
        if any("С-реактивный" in ln for ln in lines):
            return [LabResult(analyte_name="С-реактивный белок", value_num=1.8, value_raw="1.8",
                              ref_high=5.0, ref_operator="<")]
        return [LabResult(analyte_name="Гемоглобин", value_num=13.7, value_raw="13.7",
                          ref_low=11.7, ref_high=15.5)]

    monkeypatch.setattr(te, "structure_text", fake_structure)
    rows = te.extract_from_text_layer(Path("doc.pdf"))
    names = [r.analyte_name for r in rows]
    assert "С-реактивный белок" in names
    assert "Гемоглобин" in names


def test_text_layer_completeness_recovers_dropped_line(monkeypatch):
    # Вторая защита: даже на одной странице, если LLM пропустил строку-результат,
    # completeness_guard добирает её из текста слоя.
    pages = [["С-реактивный белок 1.8 мг/л <5.0", "Гемоглобин 13.7 г/дл 11.7 - 15.5"]]
    monkeypatch.setattr(te, "open_pdf", lambda p, **kw: _make_pdf_data(pages))
    # Модель вернула только гемоглобин — СРБ пропущен.
    monkeypatch.setattr(te, "structure_text", lambda lines, name: [
        LabResult(analyte_name="Гемоглобин", value_num=13.7, value_raw="13.7",
                  ref_low=11.7, ref_high=15.5)])
    rows = te.extract_from_text_layer(Path("doc.pdf"))
    names = [r.analyte_name for r in rows]
    assert "С-реактивный белок" in names  # добран стражем
    assert "Гемоглобин" in names
    assert next(r for r in rows if r.analyte_name == "С-реактивный белок").value_num == 1.8


def test_run_analysis_uses_text_layer_when_strong(monkeypatch):
    monkeypatch.setattr(ex, "_should_use_text_layer", lambda p: True)
    pages = [["Гемоглобин 13.7 г/дл 11.7 - 15.5"]]
    monkeypatch.setattr(te, "open_pdf", lambda p, **kw: _make_pdf_data(pages))
    monkeypatch.setattr(ex, "_structure_text", lambda lines, name: [
        LabResult(analyte_name="Гемоглобин", value_num=13.7, value_raw="13.7",
                  ref_low=11.7, ref_high=15.5)])
    monkeypatch.setattr(ex, "_correct_units", lambda rows: rows)
    # VLM-путь не должен вызываться
    monkeypatch.setattr(ex, "_prepare_b64",
                        lambda p: (_ for _ in ()).throw(AssertionError("VLM не должен вызываться")))
    rows = ex.run_analysis(Path("doc.pdf"))
    assert [r.analyte_name for r in rows] == ["Гемоглобин"]


def test_run_analysis_falls_back_when_text_layer_weak(monkeypatch):
    monkeypatch.setattr(ex, "_should_use_text_layer", lambda p: True)
    monkeypatch.setattr(te, "open_pdf", lambda p, **kw: _make_pdf_data([["мусор"]]))
    monkeypatch.setattr(ex, "_structure_text", lambda lines, name: [])  # слабо → 0 строк
    monkeypatch.setattr(ex, "_correct_units", lambda rows: rows)
    monkeypatch.setattr(ex, "_prepare_b64", lambda p: ["img1"])
    called = {"vlm": False}

    def fake_extract_once(images, name, low_res_retry_fn=None):
        called["vlm"] = True
        return [LabResult(analyte_name="Глюкоза", value_num=5.0)], 1

    monkeypatch.setattr(ex, "_extract_once", fake_extract_once)
    rows = ex.run_analysis(Path("doc.pdf"))
    assert called["vlm"] is True
    assert [r.analyte_name for r in rows] == ["Глюкоза"]


def test_run_analysis_falls_back_when_guard_rejects_majority(monkeypatch):
    monkeypatch.setattr(ex, "_should_use_text_layer", lambda p: True)
    monkeypatch.setattr(te, "open_pdf", lambda p, **kw: _make_pdf_data([["x"]]))
    # Обе строки с числами, которых нет в источнике → >50% выбраковки.
    monkeypatch.setattr(ex, "_structure_text", lambda lines, name: [
        LabResult(analyte_name="A", value_num=137.0, value_raw="137"),
        LabResult(analyte_name="B", value_num=999.0, value_raw="999")])
    monkeypatch.setattr(ex, "_correct_units", lambda rows: rows)
    monkeypatch.setattr(ex, "_prepare_b64", lambda p: ["img1"])
    monkeypatch.setattr(ex, "_extract_once",
                        lambda images, name, low_res_retry_fn=None: ([LabResult(analyte_name="Глюкоза", value_num=5.0)], 1))
    rows = ex.run_analysis(Path("doc.pdf"))
    assert [r.analyte_name for r in rows] == ["Глюкоза"]


def test_completeness_guard_distinguishes_same_float_with_different_precision():
    # Регрессия sample_020: float(0.6) == float(0.60), но на бланке это разные показатели.
    # Базофилы 0.6 % уже извлечены; completeness_guard не должен пропустить моноциты 0.60 ×10^9/л.
    from botkin.parsing.text_layer import completeness_guard
    rows = [
        LabResult(analyte_name="Базофилы", value_num=0.6, value_raw="0.6", unit="%",
                  ref_operator="<", ref_high=1.0, ref_text="< 1.0"),
    ]
    lines = [
        "Базофилы 0.6 % < 1.0",
        "Моноциты 0.60 * 10^9/л 0.2 - 0.95",
    ]
    recovered = completeness_guard(lines, rows)
    names = [r.analyte_name for r in recovered]
    assert "Моноциты" in names
    assert len(recovered) == 1


def test_merge_dedup_prefers_numeric_over_text_only_same_name():
    # Регрессия: одно имя с одной единицей, но текстовый и числовой варианты —
    # числовой должен заменить текстовый (например, при повторном проходе).
    from botkin.parsing.rows import merge_dedup
    base = [LabResult(analyte_name="Гемоглобин", value_num=None, value_text="отриц.",
                      value_raw="отриц.", unit=None)]
    extra = [LabResult(analyte_name="Гемоглобин", value_num=12.4, value_raw="12.4",
                       unit=None, ref_low=11.7, ref_high=15.5)]
    merged = merge_dedup(base, extra)
    assert len(merged) == 1
    assert merged[0].value_num == 12.4


def test_merge_dedup_keeps_same_name_with_different_units():
    # Регрессия sample_009: «Лимфоциты» есть в абсолютном (10^9/л) и относительном (%)
    # вариантах. Оба должны сохраниться, иначе один из показателей потеряется.
    from botkin.parsing.rows import merge_dedup
    base = [LabResult(analyte_name="Лимфоциты", value_num=1.8, value_raw="1.8",
                      unit="10^9/л")]
    extra = [LabResult(analyte_name="Лимфоциты", value_num=36.3, value_raw="36.3",
                        unit="%")]
    merged = merge_dedup(base, extra)
    assert len(merged) == 2
    assert {r.value_num for r in merged} == {1.8, 36.3}


def test_extract_unit_ref_reads_thousands_range_after_collapse():
    # _extract_unit_ref ожидает уже свёрнутый список (см. _collapse_numeric_spaces):
    # "1 010 - 1 023" → ["1010", "-", "1023"] → unit None, ref "1010 - 1023".
    from botkin.parsing.text_layer import _collapse_numeric_spaces, _extract_unit_ref
    collapsed = _collapse_numeric_spaces(["1", "010", "-", "1", "023"])
    unit, ref, _consumed = _extract_unit_ref(collapsed)
    assert unit is None
    assert ref == "1010 - 1023"


def test_parse_reference_range_handles_thousands_with_spaces():
    from botkin.parsing.scalars import parse_reference_range
    low, high, op, text = parse_reference_range("1 010 - 1 023")
    assert low == 1010.0
    assert high == 1023.0
    assert op is None
    assert text is None


def test_parse_text_line_skips_number_inside_parentheses():
    # Регрессия sample_009: «MCH (содержание Hb в 1 Эр.) 29.80 пг 27 - 34».
    # Число «1» внутри пояснения не должно считаться значением показателя.
    from botkin.parsing.text_layer import _parse_text_line
    r = _parse_text_line("MCH (содержание Hb в 1 Эр.) 29.80 пг 27 - 34")
    assert r is not None
    assert r.value_raw == "29.80"
    assert r.value_num == 29.8
    assert r.unit == "пг"
    assert r.ref_low == 27.0
    assert r.ref_high == 34.0


def test_parse_text_line_number_in_analyte_name_is_not_value():
    # Регрессия sample_001: «Антиген аденогенных раков Са 125 8.13 Ед/мл < 35 ...».
    # «125» — часть имени онкомаркера Ca 125, а не значение. Настоящее значение — 8.13.
    # Раньше completeness_guard плодил фантом «... Са = 125.0».
    from botkin.parsing.text_layer import _parse_text_line
    r = _parse_text_line("Антиген аденогенных раков Са 125 8.13 Ед/мл < 35 Cobas 6000 в крови")
    assert r is not None
    assert r.value_raw == "8.13"
    assert r.value_num == 8.13
    assert r.unit == "Ед/мл"
    assert r.ref_operator == "<"
    assert r.ref_high == 35.0
    assert "125" in r.analyte_name  # число осталось в имени аналита


def test_parse_text_line_all_keeps_ph_second_value_after_range():
    # Контроль, что фикс «числа в имени» не сломал multi-result с диапазоном:
    # «pH 5.5 5 - 8» — 5.5 значение, «5 - 8» референс (за числом идёт тире, не единица).
    from botkin.parsing.text_layer import _parse_text_line_all
    rows = _parse_text_line_all("Относительная плотность 1017 г/л 1003 - 1035 pH 5.5 5 - 8")
    by_name = {r.analyte_name: r for r in rows}
    assert by_name["pH"].value_num == 5.5
    assert by_name["pH"].ref_low == 5.0 and by_name["pH"].ref_high == 8.0


def test_num_tokens_extracts_range_numbers_without_sign():
    # Регрессия sample_008: диапазоны без пробела (35-56, 0,11-0,28) раньше
    # давали '-56' вместо '56', и _verbatim_guard отбрасывал строку.
    from botkin.parsing.scalars import num_tokens
    src = "35-56 10-16,5 0,11-0,28 0,02-0,5 15-17 0.0 - 0.1"
    tokens = set(num_tokens(src))
    assert "35" in tokens
    assert "56" in tokens
    assert "10" in tokens
    assert "16.5" in tokens
    assert "0.11" in tokens
    assert "0.28" in tokens
    assert "0.02" in tokens
    assert "0.5" in tokens
    assert "15" in tokens
    assert "17" in tokens
    assert "0" in tokens
    assert "0.1" in tokens
    # Отрицательные значения по-прежнему сохраняются, если стоят отдельно.
    assert "-0.28" in set(num_tokens("-0.28"))
