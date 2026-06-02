from botkin.bot.cards import format_card_header


def test_header_with_title_and_clinic():
    h = format_card_header({"id": 9, "doc_type": "analysis", "title": "Биохимия крови",
                            "clinic": "Инвитро", "created_at": "2026-05-28 14:30",
                            "status": "extracted"})
    assert "#9" in h and "Биохимия крови" in h and "Инвитро" in h


def test_header_fallback_title_from_doc_type():
    h = format_card_header({"id": 5, "doc_type": "prescription", "title": None,
                            "clinic": None, "created_at": "2026-05-01", "status": "extracted"})
    assert "Рецепт" in h        # лейбл из DOC_TYPE_LABELS
    assert "🏥 —" in h           # клиника не указана
