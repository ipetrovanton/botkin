"""12-factor: единый env-override через хелпер setting().

Регресс на дыру код-ревью (P2): VLM_* читали env, а IMAGE_*/DRUG_*/ANALYTE_*/
PDF_RENDER_DPI/MAX_PAGES — нет. Все скалярные настройки должны переопределяться env.
"""
import importlib

from botkin import config


def test_setting_env_override_applies_cast(monkeypatch):
    monkeypatch.setenv("BOTKIN_TEST_DPI", "333")
    assert config.setting("pdf_to_image.render_dpi", "BOTKIN_TEST_DPI", int) == 333


def test_setting_falls_back_to_default_with_cast():
    # env не задан → значение из config.json/_DEFAULTS, приведённое cast.
    val = config.setting("pdf_to_image.render_dpi", "BOTKIN_UNSET_ENV_XYZ", int)
    assert val == 200
    assert isinstance(val, int)


def test_setting_bool_cast(monkeypatch):
    monkeypatch.setenv("BOTKIN_TEST_FLAG", "yes")
    assert config.setting("vlm.structured_output", "BOTKIN_TEST_FLAG", config._as_bool) is True
    monkeypatch.setenv("BOTKIN_TEST_FLAG", "off")
    assert config.setting("vlm.structured_output", "BOTKIN_TEST_FLAG", config._as_bool) is False


def test_image_jpeg_quality_honors_env(monkeypatch):
    monkeypatch.setenv("IMAGE_JPEG_QUALITY", "55")
    importlib.reload(config)
    try:
        assert config.IMAGE_JPEG_QUALITY == 55
    finally:
        monkeypatch.delenv("IMAGE_JPEG_QUALITY", raising=False)
        importlib.reload(config)


def test_pdf_render_dpi_honors_env(monkeypatch):
    monkeypatch.setenv("PDF_RENDER_DPI", "144")
    importlib.reload(config)
    try:
        assert config.PDF_RENDER_DPI == 144
    finally:
        monkeypatch.delenv("PDF_RENDER_DPI", raising=False)
        importlib.reload(config)


def test_drug_ratio_floor_honors_env(monkeypatch):
    monkeypatch.setenv("DRUG_RATIO_FLOOR", "88")
    importlib.reload(config)
    try:
        assert config.DRUG_RATIO_FLOOR == 88.0
    finally:
        monkeypatch.delenv("DRUG_RATIO_FLOOR", raising=False)
        importlib.reload(config)
