import pytest

from botkin.normalize.numbers import parse_decimal


@pytest.mark.parametrize("raw, expected_value", [
    ("4,5", 4.5),          # десятичная запятая
    ("4.5", 4.5),
    ("145", 145.0),
    ("12,3 г/л", 12.3),    # с мусором
    ("  7,0  ", 7.0),
    ("1 234,5", 1234.5),   # пробел-разделитель тысяч
])
def test_parse_decimal_values(raw, expected_value):
    value, raw_out = parse_decimal(raw)
    assert value == expected_value
    assert raw_out == raw


def test_parse_decimal_passthrough_number():
    assert parse_decimal(4.5) == (4.5, None)
    assert parse_decimal(3) == (3.0, None)


def test_parse_decimal_none_and_garbage():
    assert parse_decimal(None) == (None, None)
    assert parse_decimal("не обнаружено") == (None, "не обнаружено")
