from botkin.bot.cards import format_rx_line


def test_active_no_warning():
    line = format_rx_line({
        "drug_mnn": "Левокарнитин", "drug_trade": "Элькар", "dose": "300 мг/мл",
        "frequency": "утром", "duration_days": 30,
        "match_status": "matched", "reg_statuses": '["active","modified"]',
    })
    assert "Левокарнитин" in line and "Элькар" in line
    assert "⚠️" not in line and "❓" not in line


def test_no_active_warns():
    line = format_rx_line({
        "drug_mnn": "Фенитоин", "drug_trade": None, "dose": None,
        "frequency": None, "duration_days": None,
        "match_status": "matched", "reg_statuses": '["expired","suspended"]',
    })
    assert "⚠️" in line
    assert "нет действующих регистраций" in line


def test_unverified_flag_with_ratio():
    line = format_rx_line({
        "drug_mnn": "Левокарнитин", "drug_trade": "Элькар", "dose": None,
        "frequency": None, "duration_days": None,
        "match_status": "unverified", "reg_statuses": None, "ratio": 0.78,
    })
    assert "❓" in line and "78" in line


def test_missing_grls_fields_safe():
    line = format_rx_line({"drug_mnn": "Аспирин", "drug_trade": None, "dose": None,
                           "frequency": None, "duration_days": None})
    assert "Аспирин" in line and "⚠️" not in line
