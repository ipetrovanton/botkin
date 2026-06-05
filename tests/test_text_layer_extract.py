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


def test_run_analysis_uses_text_layer_when_strong(monkeypatch):
    monkeypatch.setattr(ex, "_should_use_text_layer", lambda p: True)
    monkeypatch.setattr(ex, "reconstruct_lines", lambda p: ["Гемоглобин 13.7 г/дл 11.7 - 15.5"])
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
    monkeypatch.setattr(ex, "reconstruct_lines", lambda p: ["мусор"])
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
    monkeypatch.setattr(ex, "reconstruct_lines", lambda p: ["x"])
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
