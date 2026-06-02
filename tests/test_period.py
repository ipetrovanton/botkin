from datetime import datetime

from botkin.bot.period import parse_manual, preset_range


def test_preset_month():
    start, end = preset_range("month", now=datetime(2026, 6, 15, 12, 0))
    assert start == datetime(2026, 5, 15, 12, 0)
    assert end == datetime(2026, 6, 15, 12, 0)


def test_preset_all():
    start, end = preset_range("all", now=datetime(2026, 6, 15))
    assert start == datetime(1970, 1, 1)
    assert end == datetime(2026, 6, 15)


def test_parse_manual_months():
    start, end = parse_manual(["2026-01", "2026-03"])
    assert start == datetime(2026, 1, 1, 0, 0, 0)
    assert end == datetime(2026, 3, 31, 23, 59, 59)


def test_parse_manual_days():
    start, end = parse_manual(["2026-01-10", "2026-01-20"])
    assert start == datetime(2026, 1, 10, 0, 0, 0)
    assert end == datetime(2026, 1, 20, 23, 59, 59)


def test_parse_manual_invalid():
    assert parse_manual(["мусор"]) is None
    assert parse_manual([]) is None
