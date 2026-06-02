from botkin.bot.cards import format_list_body


def test_format_list_body_numbered_with_title_clinic():
    docs = [
        {"id": 23, "doc_type": "doctor_report", "title": "Заключение невролога",
         "clinic": "Клиника Здоровье", "created_at": "2026-06-02 10:00"},
        {"id": 22, "doc_type": "analysis", "title": None,
         "clinic": None, "created_at": "2026-05-28 14:30"},
    ]
    body = format_list_body(docs, offset=0, total=2)
    assert "1." in body and "2." in body
    assert "Заключение невролога" in body
    assert "Анализы" in body         # fallback названия из лейбла типа
    assert "🏥 —" in body            # клиника не указана
    assert "1–2 из 2" in body
