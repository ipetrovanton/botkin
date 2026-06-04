"""Гибридная многостраничность run_analysis: толерантность к сбою отдельной страницы.

qwen3-vl на одной странице может уйти в генерацию дублей до num_predict и вернуть
оборванный JSON → ExtractionError. Сбой добора ОДНОЙ страницы не должен ронять весь
документ и терять уже извлечённое (другая страница + общий вызов).
"""
from pathlib import Path

import botkin.llm.extract as ex
from botkin.domain.models import LabResult
from botkin.exceptions import ExtractionError


def test_page_failure_does_not_lose_document(monkeypatch):
    monkeypatch.setattr(ex, "_prepare_b64", lambda p: ["img1", "img2"])

    def fake_extract_once(images, name):
        if len(images) == 2:  # общий вызов: неполно (1 таблица < 2 страниц) → запустит добор
            return [LabResult(analyte_name="СРБ", value_num=1.8)], 1
        if name.endswith("#стр1"):
            return [LabResult(analyte_name="Гемоглобин", value_num=140.0)], 1
        raise ExtractionError("стр2: обрезанный JSON (EOF)")

    monkeypatch.setattr(ex, "_extract_once", fake_extract_once)

    rows = ex.run_analysis(Path("doc.pdf"))
    names = [r.analyte_name for r in rows]
    # стр1 и общий вызов сохранены, несмотря на падение стр2
    assert "СРБ" in names
    assert "Гемоглобин" in names


def test_salvage_json_objects_from_truncated():
    # Оборванный массив объектов: последний объект не закрыт, JSON в целом невалиден.
    truncated = (
        '{"tests": [\n'
        '  {"parameter": "Гемоглобин", "value": "146", "unit": "г/л", "reference_range": "130 - 160"},\n'
        '  {"parameter": "Гематокрит", "value": "49", "unit": "%"'  # обрыв (EOF)
    )
    objs = ex._salvage_json_objects(truncated)
    params = [o.get("parameter") for o in objs if isinstance(o, dict)]
    assert "Гемоглобин" in params  # полный объект до обрыва спасён


def test_extract_once_salvages_rows_from_truncated_response(monkeypatch):
    # _call_vlm бросает ExtractionError, но к ней приложен сырой (обрезанный) текст —
    # harvester должен спасти полные строки вместо потери всей страницы.
    truncated = (
        '{"results": [\n'
        '  {"parameter": "СОЭ", "value": "9", "unit": "мм/ч", "reference_range": "2 - 15"},\n'
        '  {"parameter": "Лейкоциты", "value": "5.1"'  # обрыв
    )

    def boom(*a, **k):
        err = ex.ExtractionError("обрезанный JSON (EOF)")
        err.raw_text = truncated
        raise err

    monkeypatch.setattr(ex, "_call_vlm", boom)
    rows, n = ex._extract_once(["img"], "doc.pdf#стр2")
    assert [r.analyte_name for r in rows] == ["СОЭ"]
