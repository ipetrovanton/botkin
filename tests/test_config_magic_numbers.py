"""P2: магические числа вынесены в config (extract/images/analytes)."""
from botkin import config


def test_raw_log_limit_exported():
    assert isinstance(config.RAW_LOG_LIMIT, int)
    assert config.RAW_LOG_LIMIT > 0


def test_text_layer_temperature_exported():
    assert isinstance(config.TEXT_LAYER_TEMPERATURE, float)
    assert config.TEXT_LAYER_TEMPERATURE == 0.0


def test_image_kernel_sizes_exported():
    assert isinstance(config.IMAGE_CLAHE_TILE, int)
    assert isinstance(config.IMAGE_UNSHARP_SIGMA, float)
    assert isinstance(config.IMAGE_DESKEW_OPEN_KERNEL, int)
    assert isinstance(config.IMAGE_DESKEW_CLOSE_KERNEL, int)
    assert config.IMAGE_DESKEW_CLOSE_KERNEL > config.IMAGE_DESKEW_OPEN_KERNEL


def test_analyte_short_key_len_exported():
    assert isinstance(config.ANALYTE_SHORT_KEY_LEN, int)
    assert config.ANALYTE_SHORT_KEY_LEN == 3
