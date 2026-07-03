"""Текстовый слой PDF: координатная сборка строк, годность, плоский текст."""
from botkin.preprocess.pdf_text import (
    has_usable_text_layer,
    reconstruct_lines,
    reconstruct_pages,
    source_text,
)


def test_reconstruct_pages_groups_lines_per_page(make_pdf, tmp_path):
    # Многостраничный PDF: строки группируются по страницам, а reconstruct_lines —
    # плоская склейка тех же страниц по порядку.
    pdf = tmp_path / "twopage.pdf"
    make_pdf(pdf, pages=[
        [(50, 100, "С-реактивный"), (160, 100, "белок"),
         (260, 100, "1.8"), (320, 100, "мг/л")],
        [(50, 100, "Гемоглобин"), (200, 100, "13.7"), (260, 100, "г/дл")],
    ])
    pages = reconstruct_pages(pdf)
    assert len(pages) == 2
    assert any("С-реактивный" in ln for ln in pages[0])
    assert all("С-реактивный" not in ln for ln in pages[1])
    assert any("Гемоглобин" in ln for ln in pages[1])
    # Плоская склейка страниц = reconstruct_lines.
    assert reconstruct_lines(pdf) == [ln for pg in pages for ln in pg]


def test_reconstruct_merges_value_offset_by_one_px(make_pdf, tmp_path):
    # Значение сидит на 1pt ниже имени (реальный кейс ИНВИТРО) — Y-толеранция
    # должна слить их в одну физическую строку.
    pdf = tmp_path / "hb.pdf"
    make_pdf(pdf, [
        (50, 100, "Гемоглобин"),
        (200, 101, "13.7"),
        (260, 100, "г/дл"),
        (320, 100, "11.7 - 15.5"),
        (50, 130, "Эритроциты"),
        (200, 130, "4.64"),
        (260, 130, "млн/мкл"),
        (320, 130, "3.8 - 5.1"),
    ])
    lines = reconstruct_lines(pdf)
    hb = [ln for ln in lines if "Гемоглобин" in ln]
    assert len(hb) == 1
    assert "13.7" in hb[0] and "г/дл" in hb[0] and "11.7" in hb[0] and "15.5" in hb[0]
    # Одна физическая строка показателя Эритроциты.
    assert sum(1 for ln in lines if "Эритроциты" in ln) == 1


def test_usable_true_for_text_pdf(make_pdf, tmp_path):
    pdf = tmp_path / "t.pdf"
    rows = [("Гемоглобин", "13.7", "г/дл"), ("Эритроциты", "4.64", "млн/мкл"),
            ("Лейкоциты", "5.15", "тыс/мкл"), ("Тромбоциты", "217", "тыс/мкл"),
            ("Гематокрит", "40.8", "%"), ("Нейтрофилы", "44.6", "%")]
    words = []
    for i, (name, val, unit) in enumerate(rows):
        y = 100 + i * 30
        words += [(50, y, name), (200, y, val), (260, y, unit)]
    make_pdf(pdf, words)
    assert has_usable_text_layer(pdf) is True


def test_usable_false_for_blank_pdf(make_pdf, tmp_path):
    pdf = tmp_path / "blank.pdf"
    make_pdf(pdf, [])  # страница без текстового слоя (скан-подобный)
    assert has_usable_text_layer(pdf) is False


def test_source_text_is_flat_and_normalized(make_pdf, tmp_path):
    pdf = tmp_path / "t.pdf"
    make_pdf(pdf, [(50, 100, "Гемоглобин"), (200, 100, "13.7")])
    txt = source_text(pdf)
    assert "Гемоглобин" in txt and "13.7" in txt


def test_reconstruct_merges_multiline_analyte_names(make_pdf, tmp_path):
    # Регрессия sample_008: PDF разбивает длинные именя показателей на две строки.
    # Вторая строка либо начинается с аббревиатуры в скобках (MCH), либо с
    # продолжения имени («объем», «эритроцитов»). Реконструкция должна склеить их.
    pdf = tmp_path / "multiline.pdf"
    make_pdf(pdf, [
        (50, 100, "Ср."), (80, 100, "содержание"), (160, 100, "гемоглобина"),
        (280, 100, "в"), (320, 100, "эритроците"),
        (50, 130, "(MCH)"), (110, 130, "30.3"), (170, 130, "pg"),
        (220, 130, "27"), (250, 130, "-"), (280, 130, "34"),
        (50, 160, "Ширина"), (110, 160, "распределения"), (220, 160, "эритроцитов"),
        (340, 160, "по"),
        (50, 190, "объем"), (120, 190, "(RDW-SD)"), (220, 190, "49.6"),
        (280, 190, "fL"), (330, 190, "35-56"),
        (50, 220, "PDW"), (100, 220, "("), (120, 220, "ширина"),
        (50, 250, "распределения"), (170, 250, "тромбоцитов"), (300, 250, ")"),
        (350, 250, "13.4"), (400, 250, "фл"), (450, 250, "9,8-16,2"),
        (50, 280, "Гемоглобин"), (200, 280, "13.7"), (260, 280, "г/л"),
        (320, 280, "11.7-15.5"),
    ])
    lines = reconstruct_lines(pdf)
    # Склеенные имена присутствуют как одна строка.
    assert any("Ср." in ln and "MCH" in ln for ln in lines)
    assert any("Ширина" in ln and "RDW-SD" in ln for ln in lines)
    assert any("PDW" in ln and "тромбоцитов" in ln for ln in lines)
    # Обычные строки не склеены с соседями.
    assert sum(1 for ln in lines if "Гемоглобин" in ln) == 1
    # После склеивания не должно остаться отдельных строк-продолжений с числами.
    assert not any("(MCH)" in ln and "Ср." not in ln for ln in lines)


def test_reconstruct_merges_name_parts_with_value_between(make_pdf, tmp_path):
    # Регрессия sample_009: длинное имя переносится на две строки, а значение
    # физически оказывается между левой частью имени и правой продолжением.
    pdf = tmp_path / "value_between.pdf"
    make_pdf(pdf, [
        (50, 100, "Расчетное"), (140, 100, "распределение"), (260, 100, "ширины"),
        (300, 105, "12.00"), (360, 105, "%"), (390, 105, "11,6"), (430, 105, "-"), (450, 105, "14,8"),
        (50, 130, "эритроцитов,"), (160, 130, "КВ"), (200, 130, "(RDW-CV)"),
    ])
    lines = reconstruct_lines(pdf)
    assert any("Расчетное" in ln and "RDW-CV" in ln and "12.00" in ln for ln in lines)


def test_reconstruct_keeps_value_outside_unclosed_parenthesis(make_pdf, tmp_path):
    # Регрессия sample_009: скобка открывается в первой части имени, значение идёт
    # между частями, а закрывающая скобка — в продолжении. Значение должно оказаться
    # за скобкой, иначе парсер не найдёт его.
    pdf = tmp_path / "paren_value_between.pdf"
    make_pdf(pdf, [
        (50, 100, "PDW"), (100, 100, "("), (120, 100, "ширина"), (200, 100, "распределения"),
        (300, 105, "13.40"), (360, 105, "фл"), (390, 105, "9,8"), (430, 105, "-"), (450, 105, "16,2"),
        (50, 130, "тромбоцитов"), (160, 130, ")"),
    ])
    lines = reconstruct_lines(pdf)
    found = next((ln for ln in lines if "PDW" in ln), None)
    assert found
    # Значение должно быть за закрывающей скобкой, а не внутри пояснения.
    assert "13.40" in found
    close_idx = found.index(")")
    value_idx = found.index("13.40")
    assert value_idx > close_idx, f"value inside parenthesis: {found}"
