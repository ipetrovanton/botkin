"""Фикстуры для тестов."""

import pymupdf
import pytest


def _make_pdf(path, words, *, page_size=(595, 842)):
    """Строит PDF: words — список (x, y, text) на одной странице.

    y — координата baseline в пунктах. Пустой words → страница без текстового слоя.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=page_size[0], height=page_size[1])
    # china-s (Droid Sans Fallback) — встроенный шрифт pymupdf с кириллицей;
    # дефолтный Helvetica кириллицу не имеет и пишет «······» в текстовый слой.
    for x, y, text in words:
        page.insert_text((x, y), text, fontsize=10, fontname="china-s")
    doc.save(str(path))
    doc.close()


@pytest.fixture
def make_pdf():
    return _make_pdf


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