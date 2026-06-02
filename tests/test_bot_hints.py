from botkin.bot.handlers.upload import photo_followup_text


def test_followup_always_has_file_hint():
    text = photo_followup_text(image_long_side=3000)
    assert "файл" in text.lower()


def test_followup_warns_on_lowres():
    text = photo_followup_text(image_long_side=720)   # < PHOTO_LOWRES_WARN
    assert "файл" in text.lower()
    assert "качеств" in text.lower() or "разрешени" in text.lower()
