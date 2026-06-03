from unittest.mock import MagicMock, patch

import pymupdf

from botkin.domain.models import ClassifyResult


def test_keep_alive_exported():
    from botkin.llm.client import default_options
    opts = default_options()
    assert "keep_alive" in opts
    assert "num_ctx" in opts and "repeat_penalty" in opts


def _tiny_pdf(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Гемоглобин 145 г/л")
    p = tmp_path / "a.pdf"
    doc.save(str(p))
    doc.close()
    return p


def test_classify_uses_small_image_and_mocked_client(tmp_path):
    from botkin.llm import classify

    fake = MagicMock()
    resp = MagicMock()
    resp.doc_type = "analysis"
    resp.confidence = 0.9
    resp.title = "Биохимия крови"
    resp.clinic = "Инвитро"
    resp._raw_response.usage.prompt_tokens = 10
    resp._raw_response.usage.completion_tokens = 5
    fake.chat.completions.create.return_value = resp

    with patch("botkin.llm.classify.get_client", return_value=fake), \
         patch("botkin.llm.classify.prepare_images", return_value=[b"\xff\xd8fakejpeg"]) as prep:
        result = classify.run_vlm(_tiny_pdf(tmp_path))

    assert isinstance(result, ClassifyResult)
    assert result.doc_type == "analysis"
    assert result.title == "Биохимия крови"
    assert result.clinic == "Инвитро"
    # classify использует уменьшенное разрешение
    from botkin.config import IMAGE_CLASSIFY_LONG_SIDE
    _, kwargs = prep.call_args
    assert kwargs.get("long_side") == IMAGE_CLASSIFY_LONG_SIDE


def test_extract_analysis_mocked(tmp_path):
    from botkin.llm import extract
    from botkin.llm.extract import RawAnalysis

    raw = RawAnalysis.model_validate({
        "tests": [{"test_name": "Клинический анализ крови", "results": [
            {"parameter": "Гемоглобин", "value": "145", "unit": "г/л", "reference_range": "120 - 160"},
        ]}],
    })
    # instructor навешивает сырой ответ на возвращаемую модель — имитируем для логирования usage.
    object.__setattr__(raw, "_raw_response",
                       MagicMock(usage=MagicMock(prompt_tokens=10, completion_tokens=5)))

    fake = MagicMock()
    fake.chat.completions.create.return_value = raw

    with patch("botkin.llm.extract.get_client", return_value=fake), \
         patch("botkin.llm.extract.prepare_images", return_value=[b"\xff\xd8fakejpeg"]) as prep:
        items = extract.run_analysis(_tiny_pdf(tmp_path))

    assert items and items[0].analyte_name == "Гемоглобин"
    assert items[0].value_num == 145.0
    assert items[0].ref_low == 120.0 and items[0].ref_high == 160.0
    _, kwargs = prep.call_args
    from botkin.config import IMAGE_EXTRACT_LONG_SIDE
    assert kwargs.get("long_side") == IMAGE_EXTRACT_LONG_SIDE
    assert kwargs.get("upscale") is True
    assert kwargs.get("deskew") is True
    assert kwargs.get("enhance") is True
