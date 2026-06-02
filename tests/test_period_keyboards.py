from botkin.bot.keyboards import period_presets_keyboard, period_view_keyboard


def _datas(m):
    return [b.callback_data for row in m.inline_keyboard for b in row]


def test_presets_keyboard():
    kb = period_presets_keyboard()
    datas = _datas(kb)
    assert "per:month:menu" in datas and "per:all:menu" in datas


def test_view_keyboard():
    kb = period_view_keyboard("month")
    datas = _datas(kb)
    assert "per:month:docs" in datas and "per:month:labs" in datas
