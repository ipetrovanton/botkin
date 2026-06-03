def test_analyte_thresholds_exported():
    from botkin import config
    assert isinstance(config.ANALYTE_MAX_EDIT_RATIO, float)
    assert isinstance(config.ANALYTE_RATIO_FLOOR, float)
    assert 0.0 < config.ANALYTE_MAX_EDIT_RATIO < 1.0
    assert 50 <= config.ANALYTE_RATIO_FLOOR <= 100
