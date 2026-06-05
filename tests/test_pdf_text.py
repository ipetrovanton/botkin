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
