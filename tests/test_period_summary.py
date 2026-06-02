from botkin.bot.cards import format_labs_summary


def test_summary_shows_trend_and_norm():
    groups = [
        {"analyte_name": "Глюкоза", "points": [
            {"value_num": 5.4, "unit": "ммоль/л", "ref_low": 3.9, "ref_high": 6.1},
            {"value_num": 4.9, "unit": "ммоль/л", "ref_low": 3.9, "ref_high": 6.1}]},
        {"analyte_name": "Холестерин", "points": [
            {"value_num": 6.8, "unit": "ммоль/л", "ref_low": None, "ref_high": 5.2}]},
    ]
    text = format_labs_summary(groups, label="3 месяца")
    assert "Глюкоза" in text and "5.4" in text and "4.9" in text     # тренд первое→последнее
    assert "Холестерин" in text and "⬆️" in text                     # выше нормы
    assert "3 месяца" in text


def test_summary_empty():
    assert "нет" in format_labs_summary([], label="месяц").lower()
