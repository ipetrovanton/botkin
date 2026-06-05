"""Структурирование текстового слоя и гейт текстового слоя в run_analysis."""
from pathlib import Path

import botkin.llm.extract as ex
from botkin.llm.extract import RawAnalysis
from botkin.domain.models import LabResult


def test_structure_text_maps_raw_to_rows(monkeypatch):
    # Модель размечает строки в RawAnalysis; маппинг → LabResult идёт через rows_from_raw.
    raw = RawAnalysis.model_validate({"results": [
        {"parameter": "Гемоглобин", "value": "13.7", "unit": "г/дл",
         "reference_range": "11.7 - 15.5"},
        {"parameter": "Эритроциты", "value": "4.64", "unit": "млн/мкл",
         "reference_range": "3.8 - 5.1"},
    ]})
    monkeypatch.setattr(ex, "_call_text", lambda messages, name: raw)
    rows = ex._structure_text(["Гемоглобин 13.7 г/дл 11.7 - 15.5",
                               "Эритроциты 4.64 млн/мкл 3.8 - 5.1"], "doc.pdf")
    names = [r.analyte_name for r in rows]
    assert names == ["Гемоглобин", "Эритроциты"]
    assert rows[0].unit == "г/дл" and rows[0].value_num == 13.7


def test_text_layer_extracts_each_page_so_lone_result_survives(monkeypatch):
    # Регресс doc#28: одинокий результат на стр.1 (С-реактивный белок) терялся при
    # едином вызове по всем страницам. Постранично — модель видит каждую страницу.
    pages = [["С-реактивный белок 1.8 мг/л <5.0"],
             ["Гемоглобин 13.7 г/дл 11.7 - 15.5"]]
    monkeypatch.setattr(ex, "reconstruct_pages", lambda p: pages)
    monkeypatch.setattr(ex, "source_text", lambda p: "\n".join(ln for pg in pages for ln in pg))

    def fake_structure(lines, name):
        # Имитация фокуса модели: видит ТОЛЬКО строки переданной страницы.
        if any("С-реактивный" in ln for ln in lines):
            return [LabResult(analyte_name="С-реактивный белок", value_num=1.8, value_raw="1.8",
                              ref_high=5.0, ref_operator="<")]
        return [LabResult(analyte_name="Гемоглобин", value_num=13.7, value_raw="13.7",
                          ref_low=11.7, ref_high=15.5)]

    monkeypatch.setattr(ex, "_structure_text", fake_structure)
    rows = ex._extract_from_text_layer(Path("doc.pdf"))
    names = [r.analyte_name for r in rows]
    assert "С-реактивный белок" in names
    assert "Гемоглобин" in names


def test_text_layer_completeness_recovers_dropped_line(monkeypatch):
    # Вторая защита: даже на одной странице, если LLM пропустил строку-результат,
    # completeness_guard добирает её из текста слоя.
    pages = [["С-реактивный белок 1.8 мг/л <5.0", "Гемоглобин 13.7 г/дл 11.7 - 15.5"]]
    monkeypatch.setattr(ex, "reconstruct_pages", lambda p: pages)
    monkeypatch.setattr(ex, "source_text", lambda p: "\n".join(pages[0]))
    # Модель вернула только гемоглобин — СРБ пропущен.
    monkeypatch.setattr(ex, "_structure_text", lambda lines, name: [
        LabResult(analyte_name="Гемоглобин", value_num=13.7, value_raw="13.7",
                  ref_low=11.7, ref_high=15.5)])
    rows = ex._extract_from_text_layer(Path("doc.pdf"))
    names = [r.analyte_name for r in rows]
    assert "С-реактивный белок" in names  # добран стражем
    assert "Гемоглобин" in names
    assert next(r for r in rows if r.analyte_name == "С-реактивный белок").value_num == 1.8


def test_run_analysis_uses_text_layer_when_strong(monkeypatch):
    monkeypatch.setattr(ex, "_should_use_text_layer", lambda p: True)
    monkeypatch.setattr(ex, "reconstruct_pages", lambda p: [["Гемоглобин 13.7 г/дл 11.7 - 15.5"]])
    monkeypatch.setattr(ex, "source_text", lambda p: "Гемоглобин 13.7 г/дл 11.7 - 15.5")
    monkeypatch.setattr(ex, "_structure_text", lambda lines, name: [
        LabResult(analyte_name="Гемоглобин", value_num=13.7, value_raw="13.7",
                  ref_low=11.7, ref_high=15.5)])
    # VLM-путь не должен вызываться
    monkeypatch.setattr(ex, "_prepare_b64",
                        lambda p: (_ for _ in ()).throw(AssertionError("VLM не должен вызываться")))
    rows = ex.run_analysis(Path("doc.pdf"))
    assert [r.analyte_name for r in rows] == ["Гемоглобин"]


def test_run_analysis_falls_back_when_text_layer_weak(monkeypatch):
    monkeypatch.setattr(ex, "_should_use_text_layer", lambda p: True)
    monkeypatch.setattr(ex, "reconstruct_pages", lambda p: [["мусор"]])
    monkeypatch.setattr(ex, "source_text", lambda p: "мусор")
    monkeypatch.setattr(ex, "_structure_text", lambda lines, name: [])  # слабо → 0 строк
    monkeypatch.setattr(ex, "_prepare_b64", lambda p: ["img1"])
    called = {"vlm": False}

    def fake_extract_once(images, name):
        called["vlm"] = True
        return [LabResult(analyte_name="Глюкоза", value_num=5.0)], 1

    monkeypatch.setattr(ex, "_extract_once", fake_extract_once)
    rows = ex.run_analysis(Path("doc.pdf"))
    assert called["vlm"] is True
    assert [r.analyte_name for r in rows] == ["Глюкоза"]


def test_run_analysis_falls_back_when_guard_rejects_majority(monkeypatch):
    monkeypatch.setattr(ex, "_should_use_text_layer", lambda p: True)
    monkeypatch.setattr(ex, "reconstruct_pages", lambda p: [["x"]])
    monkeypatch.setattr(ex, "source_text", lambda p: "тут нет таких чисел 1 2")
    # Обе строки с числами, которых нет в источнике → >50% выбраковки.
    monkeypatch.setattr(ex, "_structure_text", lambda lines, name: [
        LabResult(analyte_name="A", value_num=137.0, value_raw="137"),
        LabResult(analyte_name="B", value_num=999.0, value_raw="999")])
    monkeypatch.setattr(ex, "_prepare_b64", lambda p: ["img1"])
    monkeypatch.setattr(ex, "_extract_once",
                        lambda images, name: ([LabResult(analyte_name="Глюкоза", value_num=5.0)], 1))
    rows = ex.run_analysis(Path("doc.pdf"))
    assert [r.analyte_name for r in rows] == ["Глюкоза"]
