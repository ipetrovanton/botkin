from botkin.bot.progress import render_progress, is_terminal


def test_render_marks_current_stage():
    text = render_progress("recognizing", doc_id=9)
    assert "#9" in text
    assert "📥 Принято ✓" in text
    assert "🔍 Распознаю текст ●" in text
    assert "🧩 Нормализую данные" in text and "🧩 Нормализую данные ●" not in text


def test_render_normalizing():
    text = render_progress("normalizing", doc_id=1)
    assert "🔍 Распознаю текст ✓" in text
    assert "🧩 Нормализую данные ●" in text


def test_is_terminal():
    assert is_terminal("extracted") is True
    assert is_terminal("failed") is True
    assert is_terminal("recognizing") is False
