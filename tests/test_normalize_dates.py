from datetime import datetime

import pytest

from botkin.normalize.dates import parse_date


@pytest.mark.parametrize("raw, expected", [
    ("23 марта 2026 г.", datetime(2026, 3, 23)),
    ("23.03.2026", datetime(2026, 3, 23)),
    ("23/03/2026", datetime(2026, 3, 23)),
    ("23-03-2026", datetime(2026, 3, 23)),
    ("23.03.26", datetime(2026, 3, 23)),
    ("2026-03-23", datetime(2026, 3, 23)),
    ("2026-03-23T10:30:00", datetime(2026, 3, 23, 10, 30, 0)),
])
def test_parse_date_formats(raw, expected):
    dt, raw_out = parse_date(raw)
    assert dt == expected
    assert raw_out == raw


def test_parse_date_passthrough_datetime():
    dt = datetime(2026, 1, 1)
    assert parse_date(dt) == (dt, None)


def test_parse_date_none_and_garbage():
    assert parse_date(None) == (None, None)
    value, raw_out = parse_date("дата не указана")
    assert value is None
    assert raw_out == "дата не указана"
