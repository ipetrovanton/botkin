"""Текстовый слой PDF: координатная сборка строк, годность, плоский текст."""
from botkin.preprocess.pdf_text import reconstruct_lines


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
