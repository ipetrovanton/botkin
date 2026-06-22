"""Тесты билдера РФ-бланка make_lab_pdf (тестовая фикстура для валидации pipeline).

Билдер раскладывает результаты в табличный layout, типичный для бланков российских
лабораторий (Инвитро/Гемотест/CMD/KDL): шапка (лаборатория, пациент, дата), заголовок
панели, строки результатов по колонкам показатель|результат|ед.|референс, многостраничность.
Текстовый слой кириллический (china-s), пригоден для reconstruct_pages.
"""

from botkin.preprocess.pdf_text import has_usable_text_layer, reconstruct_pages


def test_result_row_columns_land_on_one_physical_line(make_lab_pdf, tmp_path):
    # Колонки одной строки результата лежат на одной Y → склеиваются в одну физ. строку.
    p = tmp_path / "lab.pdf"
    make_lab_pdf(
        p,
        rows=[("Гемоглобин", "13.7", "г/дл", "11.7-15.5")],
        lab="ИНВИТРО",
        patient="Иванов И.И.",
        date="2026-06-05",
    )
    pages = reconstruct_pages(p)
    assert len(pages) == 1
    flat = "\n".join(pages[0])
    # имя, значение, единица и референс — на одной физической строке.
    row_line = next(ln for ln in pages[0] if "Гемоглобин" in ln)
    assert "13.7" in row_line and "г/дл" in row_line and "11.7-15.5" in row_line
    # шапка бланка присутствует в слое.
    assert "ИНВИТРО" in flat and "Иванов И.И." in flat


def test_usable_text_layer(make_lab_pdf, tmp_path):
    p = tmp_path / "lab.pdf"
    make_lab_pdf(p, rows=[("Лейкоциты", "5.15", "×10⁹/л", "4.5-11.0")])
    assert has_usable_text_layer(p) is True


def test_pagination_splits_rows_and_loses_nothing(make_lab_pdf, tmp_path):
    # 3 строки при rows_per_page=2 → 2 страницы, ни одна строка не потеряна.
    p = tmp_path / "lab.pdf"
    rows = [
        ("Гемоглобин", "13.7", "г/дл", "11.7-15.5"),
        ("Эритроциты", "4.64", "×10¹²/л", "3.8-5.1"),
        ("СОЭ", "9", "мм/ч", "<20"),
    ]
    make_lab_pdf(p, rows=rows, rows_per_page=2)
    pages = reconstruct_pages(p)
    assert len(pages) == 2
    flat = "\n".join(ln for page in pages for ln in page)
    for row in rows:
        assert row[0] in flat and row[1] in flat


def test_panel_title_rendered(make_lab_pdf, tmp_path):
    p = tmp_path / "lab.pdf"
    make_lab_pdf(
        p,
        rows=[("Тромбоциты", "217", "×10⁹/л", "150-400")],
        title="Общий анализ крови",
    )
    flat = "\n".join(reconstruct_pages(p)[0])
    assert "Общий анализ крови" in flat
