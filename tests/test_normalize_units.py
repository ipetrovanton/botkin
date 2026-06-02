from botkin.normalize.units import canonical_unit


def test_canonical_known_variants():
    assert canonical_unit("10^9/L")[0] == "×10⁹/л"
    assert canonical_unit("×10^9/л")[0] == "×10⁹/л"
    assert canonical_unit("тыс/мкл")[0] == "×10⁹/л"
    assert canonical_unit("g/l")[0] == "г/л"


def test_canonical_preserves_raw():
    canon, raw = canonical_unit("10^9/L")
    assert raw == "10^9/L"


def test_canonical_unknown_passthrough():
    canon, raw = canonical_unit("ммоль/л")
    assert canon == "ммоль/л"   # неизвестное остаётся как есть
    assert raw == "ммоль/л"


def test_canonical_none():
    assert canonical_unit(None) == (None, None)
