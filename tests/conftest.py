"""Фикстуры для тестов."""

import pymupdf
import pytest


def _make_pdf(path, words=None, *, pages=None, page_size=(595, 842)):
    """Строит PDF.

    words — список (x, y, text) на ОДНОЙ странице (обратная совместимость).
    pages — список таких списков, по одной странице на элемент (многостраничный PDF).
    y — координата baseline в пунктах. Пустой список слов → страница без текстового слоя.
    """
    page_words = pages if pages is not None else [words or []]
    doc = pymupdf.open()
    for ws in page_words:
        page = doc.new_page(width=page_size[0], height=page_size[1])
        # china-s (Droid Sans Fallback) — встроенный шрифт pymupdf с кириллицей;
        # дефолтный Helvetica кириллицу не имеет и пишет «······» в текстовый слой.
        for x, y, text in ws:
            page.insert_text((x, y), text, fontsize=10, fontname="china-s")
    doc.save(str(path))
    doc.close()


@pytest.fixture
def make_pdf():
    return _make_pdf


# Геометрия табличного бланка (A4 595×842 pt). Колонки на фиксированных X, чтобы
# reconstruct_pages склеивал показатель|результат|ед.|референс в одну физ. строку,
# а строки результатов (шаг _ROW_STEP=18 pt > y_tol=3) кластеризовались раздельно.
_COL_NAME_X = 40
_COL_VALUE_X = 300
_COL_UNIT_X = 370
_COL_REF_X = 450
_HEADER_Y = 40
_ROWS_TOP_Y = 130
_ROW_STEP = 18


def _lab_page_words(lab, patient, date, title, rows):
    """Один лист бланка → список слов (x, y, text) с реалистичной раскладкой РФ-бланка."""
    ws = [
        (_COL_NAME_X, _HEADER_Y, lab),
        (_COL_NAME_X, _HEADER_Y + 18, f"Пациент: {patient}"),
        (_COL_NAME_X, _HEADER_Y + 32, f"Дата взятия: {date}"),
    ]
    if title:
        ws.append((_COL_NAME_X, _HEADER_Y + 56, title))
    # шапка таблицы
    head_y = _ROWS_TOP_Y - _ROW_STEP
    ws += [
        (_COL_NAME_X, head_y, "Показатель"),
        (_COL_VALUE_X, head_y, "Результат"),
        (_COL_UNIT_X, head_y, "Ед."),
        (_COL_REF_X, head_y, "Референс"),
    ]
    for i, (name, value, unit, ref) in enumerate(rows):
        y = _ROWS_TOP_Y + i * _ROW_STEP
        ws.append((_COL_NAME_X, y, name))
        ws.append((_COL_VALUE_X, y, value))
        if unit:
            ws.append((_COL_UNIT_X, y, unit))
        if ref:
            ws.append((_COL_REF_X, y, ref))
    return ws


def _make_lab_pdf(
    path,
    rows,
    *,
    lab="ИНВИТРО",
    patient="Иванов И.И.",
    date="2026-06-05",
    title=None,
    rows_per_page=None,
):
    """Строит PDF-бланк российской лаборатории с табличной раскладкой.

    rows — список (name, value, unit, ref); пустые unit/ref пропускаются. Каждая строка
    результата кладётся на одну Y, колонки разнесены по X (показатель|результат|ед.|референс).
    rows_per_page — если задан, строки бьются на страницы по N (шапка повторяется на каждой);
    это позволяет воспроизводить многостраничные бланки, где одиночный результат на
    отдельной странице терялся (см. С-реактивный белок, итер.12–13).
    """
    chunks = (
        [rows[i : i + rows_per_page] for i in range(0, len(rows), rows_per_page)]
        if rows_per_page
        else [rows]
    ) or [[]]
    pages = [_lab_page_words(lab, patient, date, title, chunk) for chunk in chunks]
    _make_pdf(path, pages=pages)


@pytest.fixture
def make_lab_pdf():
    return _make_lab_pdf


@pytest.fixture
def tmp_db_path(tmp_path):
    """Временный путь к БД."""
    return tmp_path / "test.db"


@pytest.fixture
def set_test_db(monkeypatch, tmp_db_path):
    """Подменяет SQLITE_PATH на временную БД."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_db_path))

    import importlib
    import botkin.config
    import botkin.db.connection

    importlib.reload(botkin.config)
    importlib.reload(botkin.db.connection)

    botkin.db.connection.init_db()
    return tmp_db_path