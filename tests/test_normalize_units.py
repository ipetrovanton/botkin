from botkin.normalize.units import canonical_unit


def test_canonical_known_variants():
    assert canonical_unit("10^9/L")[0] == "×10⁹/л"
    assert canonical_unit("×10^9/л")[0] == "×10⁹/л"
    assert canonical_unit("тыс/мкл")[0] == "×10⁹/л"
    assert canonical_unit("g/l")[0] == "г/л"


def test_canonical_superscript_powers_converge_with_ascii():
    # Текстовый слой PDF отдаёт Unicode-надстрочную форму (×10⁹/л), а реестр ФСЛИ
    # хранит ASCII (10^9/л). Обе должны свестись к одному канону, иначе при сверке
    # единицы документа с expected_units показателя возникает ложный unit_mismatch.
    assert canonical_unit("×10⁹/л")[0] == "×10⁹/л"
    assert canonical_unit("10^9/л")[0] == "×10⁹/л"
    assert canonical_unit("×10¹²/л")[0] == "×10¹²/л"
    assert canonical_unit("10^12/л")[0] == "×10¹²/л"


def test_canonical_superscript_without_multiplication_sign():
    # Часть бланков пишет степень надстрочными цифрами без знака ×: «10⁹/л».
    # Канонизатор обязан свернуть её алгоритмически, не полагаясь на ручной алиас.
    assert canonical_unit("10⁹/л")[0] == "×10⁹/л"
    assert canonical_unit("10¹²/л")[0] == "×10¹²/л"


def test_canonical_preserves_raw():
    canon, raw = canonical_unit("10^9/L")
    assert raw == "10^9/L"


def test_canonical_unknown_passthrough():
    canon, raw = canonical_unit("ммоль/л")
    assert canon == "ммоль/л"   # неизвестное остаётся как есть
    assert raw == "ммоль/л"


def test_canonical_none():
    assert canonical_unit(None) == (None, None)
