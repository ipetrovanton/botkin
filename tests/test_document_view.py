"""P2: общий рендер карточки документа в нейтральном модуле (разрыв цикла импорта).

Композиция «шапка + разделитель + детали» дублировалась в show/upload/browse, а
_format_document тащился отложенным импортом внутри функций. Сводим в bot.document_view.
"""
from botkin.bot import document_view


def test_compose_card_has_header_separator_and_details():
    doc = {"id": 7, "doc_type": "unknown", "user_id": 1, "status": "extracted"}
    text = document_view.compose_card(7, doc)
    assert "Документ #7" in text
    assert "────────────" in text
    assert "не поддерживается" in text  # детали для unknown-типа


def test_show_still_reexports_format_helpers():
    # обратная совместимость: тесты и хендлеры импортируют их из show
    from botkin.bot.handlers.show import _format_document, _format_labs, _format_ref
    assert callable(_format_document)
    assert callable(_format_labs)
    assert callable(_format_ref)
