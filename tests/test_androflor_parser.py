import botkin.llm.extract as ex
from botkin.domain.models import LabResult
from botkin.parsing.androflor import is_androflor_text, parse_androflor_ocr


_ANDROFLOR_OCR = """
Геномная ДНК человека: 10 5.7
Общая бактериальная масса: 10 4.8
Lactobacillus spp.: 10 4.7 -0.1 (68-91%)
Corynebacterium spp.: 10 3.6 -1.2 (5-7%)
Gardnerella vaginalis: не выявлено
Megasphaera spp. / Veillonella spp. / Dialister spp.: 10 3.6 -1.2 (5-7%)
Bacteroides spp. / Porphyromonas spp. / Prevotella spp.: 10 4.0 -0.8 (13-18%)
Anaerococcus spp.: 10 3.4 -1.4 (3-5%)
Eubacterium spp.: 10 4.1 -0.7 (17-23%)
Сумма: УПМ анаэробы: 10 4.4 -0.4 (34-46%)
"""


# Бесколоночный формат — qwen3-vl на части прогонов выдаёт строку БЕЗ двоеточия после имени.
# Это был реальный баг: regex требовал ':' и давал 0 строк → маршрут уходил в _structure_text,
# портивший Lg-нотацию "10 5.7" в 10.0. Парсер обязан понимать формат и без двоеточия.
_ANDROFLOR_OCR_NO_COLON = """
Геномная ДНК человека 10 5.7
Общая бактериальная масса 10 4.8
Lactobacillus spp. 10 4.7 -0.1 (68-91%)
Staphylococcus spp. не выявлено
Сумма: Нормофлора 10 3.6 -1.2 (5-7%)
Сумма: УПМ анаэробы 10 4.4 -0.4 (34-46%)
"""

# Многострочный формат — модель эхом повторяет колонки таблицы (наблюдается на части
# прогонов qwen3-vl). Парсер обязан понимать его наравне с однострочным.
_ANDROFLOR_OCR_MULTILINE = """
Название показателя: Геномная ДНК человека
Количественный результат: 10 5.7
Относительный Lg(X/CBMO): —
% от CBMO: 100

Название показателя: Lactobacillus spp.
Количественный результат: 10 4.7
Относительный Lg(X/CBMO): -0.1 (68-91%)
% от CBMO: 100

Название показателя: Staphylococcus spp.
Количественный результат: не выявлено

Название показателя: Eubacterium spp.
Количественный результат: 10 4.1
Относительный Lg(X/CBMO): -0.7 (17-23%)
"""

# Страница-описание бланка Андрофлор: маркеры есть, но это проза, не таблица.
_ANDROFLOR_DESCRIPTION = """
Исследование микрофлоры урогенитального тракта мужчин методом ПЦР. Андрофлор.
Количественную оценку всех бактерий (общая бактериальная масса) и нормофлоры выполняют...
* Horner PJ et al. 2016 European guideline. Int J STD AIDS, 2016 Oct;27(11): 928
"""


def test_is_androflor_text_detects_ocr_table():
    assert is_androflor_text(_ANDROFLOR_OCR)
    assert not is_androflor_text("Гемоглобин 13.7 г/дл")


def test_parse_androflor_ocr_handles_format_without_colon():
    rows = parse_androflor_ocr(_ANDROFLOR_OCR_NO_COLON)
    by_name = {row.analyte_name: row for row in rows}

    assert by_name["Геномная ДНК человека"].value_num == 5.7
    assert by_name["Геномная ДНК человека"].unit == "Lg"
    assert by_name["Общая бактериальная масса"].value_num == 4.8
    assert by_name["Lactobacillus spp."].value_num == 4.7
    assert by_name["Lactobacillus spp., относительный показатель"].value_num == -0.1
    # внутреннее двоеточие в имени сохраняется, имя не теряется
    assert by_name["Сумма: Нормофлора"].value_num == 3.6
    assert by_name["Сумма: УПМ анаэробы"].value_num == 4.4
    assert by_name["Сумма: УПМ анаэробы, относительный показатель"].value_num == -0.4
    assert "Staphylococcus spp." not in by_name


def test_parse_androflor_ocr_handles_multiline_format():
    rows = parse_androflor_ocr(_ANDROFLOR_OCR_MULTILINE)
    by_name = {row.analyte_name: row for row in rows}

    assert by_name["Геномная ДНК человека"].value_num == 5.7
    assert by_name["Геномная ДНК человека"].unit == "Lg"
    assert by_name["Lactobacillus spp."].value_num == 4.7
    assert by_name["Lactobacillus spp., относительный показатель"].value_num == -0.1
    assert by_name["Eubacterium spp."].value_num == 4.1
    assert by_name["Eubacterium spp., относительный показатель"].value_num == -0.7
    # "не выявлено" и "—" не превращаются в числовые строки
    assert "Staphylococcus spp." not in by_name
    assert "Геномная ДНК человека, относительный показатель" not in by_name


def test_parse_androflor_ocr_ignores_description_prose():
    # Проза описания бланка не должна давать осмысленных табличных строк.
    rows = parse_androflor_ocr(_ANDROFLOR_DESCRIPTION)
    assert len(rows) <= 1  # максимум случайная мусорная строка из ссылки


def test_parse_androflor_ocr_extracts_lg_and_relative_values():
    rows = parse_androflor_ocr(_ANDROFLOR_OCR)
    by_name = {row.analyte_name: row for row in rows}

    assert by_name["Геномная ДНК человека"].value_num == 5.7
    assert by_name["Геномная ДНК человека"].unit == "Lg"
    assert by_name["Общая бактериальная масса"].value_num == 4.8
    assert by_name["Lactobacillus spp."].value_num == 4.7
    assert by_name["Lactobacillus spp., относительный показатель"].value_num == -0.1
    assert by_name["Lactobacillus spp., относительный показатель"].unit == "Lg(X/СВМО)"
    assert by_name["Bacteroides spp. / Porphyromonas spp. / Prevotella spp."].value_num == 4.0
    assert by_name["Bacteroides spp. / Porphyromonas spp. / Prevotella spp., относительный показатель"].value_num == -0.8
    assert "Gardnerella vaginalis" not in by_name
    assert len(rows) == 16


def test_extract_once_uses_ocr_primary_path(monkeypatch):
    """OCR-путь первичен: если он вернул строки, structured VLM не вызывается."""
    calls = {"ocr": 0, "vlm": 0}

    def fake_ocr(images, doc_name):
        calls["ocr"] += 1
        return [LabResult(analyte_name="Геномная ДНК человека", value_num=5.7, unit="Lg")]

    def fake_vlm_attempt(messages, doc_name, structured=None):
        calls["vlm"] += 1
        return [], 0

    monkeypatch.setattr(ex, "_ocr_then_structure", fake_ocr)
    monkeypatch.setattr(ex, "_vlm_extract_attempt", fake_vlm_attempt)

    rows, tables = ex._extract_once(["img"], "sample_006#стр1")

    assert calls["ocr"] == 1
    assert calls["vlm"] == 0
    assert tables == 1
    assert rows[0].value_num == 5.7


def test_extract_once_falls_back_to_vlm_when_ocr_empty(monkeypatch):
    """OCR вернул пусто → фолбэк на structured VLM."""
    calls = {"ocr": 0, "vlm": 0}
    expected = [LabResult(analyte_name="Гемоглобин", value_num=13.7)]

    def fake_ocr(images, doc_name):
        calls["ocr"] += 1
        return []

    def fake_vlm_attempt(messages, doc_name, structured=None):
        calls["vlm"] += 1
        return expected, 1

    monkeypatch.setattr(ex, "_ocr_then_structure", fake_ocr)
    monkeypatch.setattr(ex, "_vlm_extract_attempt", fake_vlm_attempt)

    rows, tables = ex._extract_once(["img"], "normal")

    assert calls["ocr"] == 1
    assert calls["vlm"] == 1
    assert rows == expected


def test_extract_once_falls_back_to_vlm_when_ocr_raises(monkeypatch):
    """Падение OCR-вызова (сеть/модель) не валит документ — фолбэк на VLM."""
    calls = {"vlm": 0}

    def fake_ocr(images, doc_name):
        raise RuntimeError("ollama down")

    def fake_vlm_attempt(messages, doc_name, structured=None):
        calls["vlm"] += 1
        return [LabResult(analyte_name="Гемоглобин", value_num=13.7)], 1

    monkeypatch.setattr(ex, "_ocr_then_structure", fake_ocr)
    monkeypatch.setattr(ex, "_vlm_extract_attempt", fake_vlm_attempt)

    rows, tables = ex._extract_once(["img"], "sample_006#стр1")

    assert calls["vlm"] == 1
    assert rows[0].value_num == 13.7


def test_ocr_then_structure_routes_androflor_to_parser(monkeypatch):
    """Андрофлор-текст → доменный parser (без вызова общего text-LLM структурирования)."""
    calls = {"structure": 0}
    monkeypatch.setattr(ex, "_call_image_ocr", lambda images, doc_name: _ANDROFLOR_OCR)
    monkeypatch.setattr(ex, "_structure_text", lambda lines, doc_name: calls.__setitem__("structure", 1) or [])

    rows = ex._ocr_then_structure(["img"], "sample_006#стр1")

    assert calls["structure"] == 0
    assert any(r.analyte_name == "Геномная ДНК человека" and r.value_num == 5.7 for r in rows)


def test_ocr_then_structure_routes_generic_to_structure_text(monkeypatch):
    """Неандрофлор-текст → общий text-LLM путь _structure_text."""
    captured = {}

    def fake_structure(lines, doc_name):
        captured["lines"] = lines
        return [LabResult(analyte_name="Гемоглобин", value_num=153.0, unit="г/л")]

    monkeypatch.setattr(ex, "_call_image_ocr", lambda images, doc_name: "Гемоглобин: 153 г/л\nЭритроциты: 4.66")
    monkeypatch.setattr(ex, "_structure_text", fake_structure)

    rows = ex._ocr_then_structure(["img"], "sample_011#стр1")

    assert captured["lines"][0].startswith("Гемоглобин")
    assert rows[0].value_num == 153.0


def test_ocr_then_structure_skips_androflor_description_page(monkeypatch):
    """Страница-описание бланка (маркеры есть, таблицы нет) → пусто, БЕЗ ухода в _structure_text.

    Иначе общий _structure_text портит Lg-нотацию в 10.0 и/или вносит мусорные строки из прозы.
    """
    calls = {"structure": 0}
    monkeypatch.setattr(ex, "_call_image_ocr", lambda images, doc_name: _ANDROFLOR_DESCRIPTION)
    monkeypatch.setattr(
        ex, "_structure_text",
        lambda lines, doc_name: calls.__setitem__("structure", 1) or [LabResult(analyte_name="мусор", value_num=10.0)],
    )

    rows = ex._ocr_then_structure(["img"], "sample_006#стр2")

    assert calls["structure"] == 0
    assert rows == []
