"""Структурирование текстового слоя и гейт текстового слоя в run_analysis."""
import botkin.llm.extract as ex
from botkin.llm.extract import RawAnalysis


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
