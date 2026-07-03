"""Гибридная многостраничность run_analysis: толерантность к сбою отдельной страницы.

qwen3-vl на одной странице может уйти в генерацию дублей до num_predict и вернуть
оборванный JSON → ExtractionError. Сбой добора ОДНОЙ страницы не должен ронять весь
документ и терять уже извлечённое (другая страница + общий вызов).
"""
from pathlib import Path

import botkin.llm.extract as ex
from botkin.domain.models import LabResult
from botkin.exceptions import ExtractionError


def test_multipage_reads_each_page_once_no_combined_call(monkeypatch):
    # Многостраничный документ должен читаться ПОСТРАНИЧНО (каждая страница 1 раз),
    # без общего вызова со всеми изображениями — иначе страницы читаются дважды.
    monkeypatch.setattr(ex, "_prepare_b64", lambda p: ["img1", "img2"])
    calls = []

    def fake_extract_once(images, name):
        calls.append(len(images))
        return [LabResult(analyte_name=f"Показатель {name[-1]}", value_num=1.0)], 1

    monkeypatch.setattr(ex, "_extract_once", fake_extract_once)
    ex.run_analysis(Path("doc.pdf"))
    assert calls == [1, 1]  # ровно два вызова по одной странице, нет вызова с 2 изображениями


def test_page_failure_does_not_lose_document(monkeypatch):
    monkeypatch.setattr(ex, "_prepare_b64", lambda p: ["img1", "img2"])

    def fake_extract_once(images, name):
        if name.endswith("#стр1"):
            return [LabResult(analyte_name="Гемоглобин", value_num=140.0)], 1
        raise ExtractionError("стр2: обрезанный JSON (EOF)")

    monkeypatch.setattr(ex, "_extract_once", fake_extract_once)

    rows = ex.run_analysis(Path("doc.pdf"))
    names = [r.analyte_name for r in rows]
    # стр1 сохранена, несмотря на падение стр2
    assert "Гемоглобин" in names


def test_single_page_uses_one_combined_call(monkeypatch):
    # Одностраничный — один вызов (постраничный режим не нужен).
    monkeypatch.setattr(ex, "_prepare_b64", lambda p: ["only"])
    calls = []

    def fake_extract_once(images, name):
        calls.append(len(images))
        return [LabResult(analyte_name="Глюкоза", value_num=5.0)], 1

    monkeypatch.setattr(ex, "_extract_once", fake_extract_once)
    ex.run_analysis(Path("doc.pdf"))
    assert calls == [1]


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


def test_merge_dedup_by_name_drops_conflicting_value():
    # Между общим вызовом и постраничным добором модель даёт РАЗНЫЕ числа для одного
    # показателя (Гемоглобин 13.7 г/дл vs 143 г/л). Слияние по имени: дубль отбрасываем,
    # доверяем первому (общему) проходу; реально новый показатель добавляем.
    base = [ex.LabResult(analyte_name="Гемоглобин", value_num=13.7, unit="г/дл")]
    extra = [
        ex.LabResult(analyte_name="Гемоглобин", value_num=143.0, unit="г/л"),  # дубль по имени
        ex.LabResult(analyte_name="СОЭ", value_num=9.0, unit="мм/ч"),          # новый показатель
    ]
    merged = ex._merge_dedup(base, extra)
    names = [r.analyte_name for r in merged]
    assert names.count("Гемоглобин") == 1     # конфликтующий дубль отброшен
    assert merged[0].value_num == 13.7        # значение общего (первого) прохода сохранено
    assert "СОЭ" in names                      # реально новый показатель добавлен


def test_merge_dedup_name_normalized():
    # Регистр и ё/е не должны плодить дубли при слиянии.
    base = [ex.LabResult(analyte_name="Гемоглобин", value_num=140.0)]
    extra = [ex.LabResult(analyte_name="гемоглобин", value_num=140.0)]
    assert len(ex._merge_dedup(base, extra)) == 1
