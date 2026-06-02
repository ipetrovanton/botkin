from botkin.bot.keyboards import card_keyboard, list_keyboard


def _texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def _datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_list_keyboard_has_filters_numbers_and_paging():
    ids = [11, 12, 13]
    kb = list_keyboard(ids, doc_type=None, offset=0, total=20)
    texts = _texts(kb)
    # фильтры — эмодзи С подписью, чтобы было понятно, где что
    assert "🧪 Анализы" in texts
    assert "👨‍⚕️ Заключения" in texts
    assert "📋 Все" in texts
    assert not any("Рецепт" in t for t in texts)   # рецепты сняты с поддержки
    assert "1" in texts and "3" in texts          # номера по количеству на странице
    assert any("Вперёд" in t for t in texts)      # есть следующая страница
    assert not any("Назад" in t for t in texts)   # на offset=0 назад нет


def test_card_keyboard_nav():
    kb = card_keyboard(doc_id=12, has_prev=True, has_next=False)
    datas = _datas(kb)
    assert "nav:12:prev" in datas
    assert "lst:all:0" in datas                   # кнопка «к списку»
    assert "nav:12:next" not in datas             # нет следующего
