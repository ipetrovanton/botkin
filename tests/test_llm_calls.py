from unittest.mock import MagicMock, patch

import pymupdf

from botkin.domain.models import ClassifyResult


def test_keep_alive_exported():
    from botkin.llm.client import default_options
    opts = default_options()
    assert "keep_alive" in opts
    assert "num_ctx" in opts and "repeat_penalty" in opts


def test_retry_policy_retries_parse_error_then_succeeds():
    """tenacity-политика: провал парсинга JSON ретраится, успех на 2-й попытке."""
    from json import JSONDecodeError

    from botkin.llm.client import build_retrying

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise JSONDecodeError("bad", "", 0)
        return "ok"

    result = build_retrying(initial_wait=0, max_wait=0, attempts=3)(flaky)
    assert result == "ok"
    assert calls["n"] == 2


def test_retry_policy_does_not_retry_non_validation_errors():
    """4xx-контент (битый запрос) не ретраим — попытка ровно одна."""
    import pytest

    from botkin.llm.client import build_retrying

    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("400 Bad Request — изображение слишком большое")

    with pytest.raises(Exception):
        build_retrying(initial_wait=0, max_wait=0, attempts=3)(boom)
    assert calls["n"] == 1


def test_retry_policy_stops_after_attempts():
    """Стоп по числу попыток: упорный провал валидации не крутит вечно."""
    import pytest
    from json import JSONDecodeError

    from botkin.llm.client import build_retrying

    calls = {"n": 0}

    def always_bad():
        calls["n"] += 1
        raise JSONDecodeError("bad", "", 0)

    with pytest.raises(Exception):
        build_retrying(initial_wait=0, max_wait=0, attempts=2)(always_bad)
    assert calls["n"] == 2


def test_retry_policy_raises_retryerror_not_bare_on_exhaustion():
    """Контракт с instructor: на исчерпании летит tenacity RetryError, а не голый
    ValidationError. instructor ловит RetryError и оборачивает в InstructorRetryException
    с last_completion — без этого ломается salvage обрезанного JSON. (reraise=False)."""
    import pytest
    from json import JSONDecodeError

    from tenacity import RetryError

    from botkin.llm.client import build_retrying

    def always_bad():
        raise JSONDecodeError("bad", "", 0)

    with pytest.raises(RetryError):
        build_retrying(initial_wait=0, max_wait=0, attempts=2)(always_bad)


def _tiny_pdf(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Гемоглобин 145 г/л")
    p = tmp_path / "a.pdf"
    doc.save(str(p))
    doc.close()
    return p


def test_extract_passes_tenacity_retrying_as_max_retries(tmp_path):
    """instructor получает tenacity-Retrying (backoff+jitter), а не голый int."""
    from tenacity import Retrying

    from botkin.llm import extract
    from botkin.llm.extract import RawAnalysis

    raw = RawAnalysis.model_validate({
        "results": [{"parameter": "Гемоглобин", "value": "145", "unit": "г/л"}]})
    object.__setattr__(raw, "_raw_response",
                       MagicMock(usage=MagicMock(prompt_tokens=1, completion_tokens=1)))
    fake = MagicMock()
    fake.chat.completions.create.return_value = raw

    with patch("botkin.llm.extract.get_client", return_value=fake), \
         patch("botkin.llm.extract.prepare_images", return_value=[b"\xff\xd8fakejpeg"]):
        extract.run_analysis(_tiny_pdf(tmp_path))

    _, kwargs = fake.chat.completions.create.call_args
    assert isinstance(kwargs["max_retries"], Retrying)


def test_classify_passes_tenacity_retrying_as_max_retries(tmp_path):
    """Классификатор тоже ретраит через tenacity, а не захардкоженный int."""
    from tenacity import Retrying

    from botkin.llm import classify

    resp = MagicMock()
    resp.doc_type = "analysis"
    resp.confidence = 0.9
    resp.title = None
    resp.clinic = None
    resp._raw_response.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    fake = MagicMock()
    fake.chat.completions.create.return_value = resp

    with patch("botkin.llm.classify.get_client", return_value=fake), \
         patch("botkin.llm.classify.prepare_images", return_value=[b"\xff\xd8fakejpeg"]):
        classify.run_vlm(_tiny_pdf(tmp_path))

    _, kwargs = fake.chat.completions.create.call_args
    assert isinstance(kwargs["max_retries"], Retrying)


def test_call_passes_native_format_schema_when_enabled(tmp_path):
    """VLM_STRUCTURED_OUTPUT=on → в Ollama уходит нативный format=JSON-схема (XGrammar)."""
    from botkin.llm import extract
    from botkin.llm.extract import RawAnalysis

    raw = RawAnalysis.model_validate({
        "results": [{"parameter": "Гемоглобин", "value": "145", "unit": "г/л"}]})
    object.__setattr__(raw, "_raw_response",
                       MagicMock(usage=MagicMock(prompt_tokens=1, completion_tokens=1)))
    fake = MagicMock()
    fake.chat.completions.create.return_value = raw

    with patch("botkin.llm.extract.get_client", return_value=fake), \
         patch("botkin.llm.extract.prepare_images", return_value=[b"\xff\xd8fakejpeg"]), \
         patch("botkin.llm.client.VLM_STRUCTURED_OUTPUT", True):
        extract.run_analysis(_tiny_pdf(tmp_path))

    _, kwargs = fake.chat.completions.create.call_args
    assert kwargs["extra_body"]["format"] == RawAnalysis.model_json_schema()
    assert "options" in kwargs["extra_body"]   # опции Ollama не потеряны


def test_call_omits_format_when_disabled(tmp_path):
    """VLM_STRUCTURED_OUTPUT=off → format не отправляется (откат на prompt-only JSON)."""
    from botkin.llm import extract
    from botkin.llm.extract import RawAnalysis

    raw = RawAnalysis.model_validate({
        "results": [{"parameter": "Гемоглобин", "value": "145", "unit": "г/л"}]})
    object.__setattr__(raw, "_raw_response",
                       MagicMock(usage=MagicMock(prompt_tokens=1, completion_tokens=1)))
    fake = MagicMock()
    fake.chat.completions.create.return_value = raw

    with patch("botkin.llm.extract.get_client", return_value=fake), \
         patch("botkin.llm.extract.prepare_images", return_value=[b"\xff\xd8fakejpeg"]), \
         patch("botkin.llm.client.VLM_STRUCTURED_OUTPUT", False):
        extract.run_analysis(_tiny_pdf(tmp_path))

    _, kwargs = fake.chat.completions.create.call_args
    assert "format" not in kwargs["extra_body"]


def test_classify_passes_native_format_schema_when_enabled(tmp_path):
    """Классификатор тоже принуждает схему нативным format при включённом флаге."""
    from botkin.llm import classify
    from botkin.llm.classify import ClassifySchema

    resp = MagicMock()
    resp.doc_type = "analysis"
    resp.confidence = 0.9
    resp.title = None
    resp.clinic = None
    resp._raw_response.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    fake = MagicMock()
    fake.chat.completions.create.return_value = resp

    with patch("botkin.llm.classify.get_client", return_value=fake), \
         patch("botkin.llm.classify.prepare_images", return_value=[b"\xff\xd8fakejpeg"]), \
         patch("botkin.llm.client.VLM_STRUCTURED_OUTPUT", True):
        classify.run_vlm(_tiny_pdf(tmp_path))

    _, kwargs = fake.chat.completions.create.call_args
    assert kwargs["extra_body"]["format"] == ClassifySchema.model_json_schema()


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


def test_classify_survives_missing_usage(tmp_path):
    """Недоступный usage не должен валить успешную классификацию (usage — только для лога)."""
    from botkin.llm import classify

    resp = MagicMock()
    resp.doc_type = "analysis"
    resp.confidence = 0.9
    resp.title = None
    resp.clinic = None
    resp._raw_response.usage = None   # счётчики токенов недоступны

    fake = MagicMock()
    fake.chat.completions.create.return_value = resp

    with patch("botkin.llm.classify.get_client", return_value=fake), \
         patch("botkin.llm.classify.prepare_images", return_value=[b"\xff\xd8fakejpeg"]):
        result = classify.run_vlm(_tiny_pdf(tmp_path))

    assert result.doc_type == "analysis"


def test_extract_survives_missing_usage(tmp_path):
    """Недоступный usage не должен валить успешное извлечение."""
    from botkin.llm import extract
    from botkin.llm.extract import RawAnalysis

    raw = RawAnalysis.model_validate({
        "results": [{"parameter": "Гемоглобин", "value": "145", "unit": "г/л"}],
    })
    object.__setattr__(raw, "_raw_response", MagicMock(usage=None))

    fake = MagicMock()
    fake.chat.completions.create.return_value = raw

    with patch("botkin.llm.extract.get_client", return_value=fake), \
         patch("botkin.llm.extract.prepare_images", return_value=[b"\xff\xd8fakejpeg"]):
        items = extract.run_analysis(_tiny_pdf(tmp_path))

    assert items and items[0].analyte_name == "Гемоглобин"


def test_extract_logs_prompt_version(tmp_path, caplog):
    """Версия промптов попадает в лог успешного извлечения — для воспроизводимости."""
    import logging

    from botkin.llm import extract
    from botkin.llm.extract import RawAnalysis
    from botkin.llm.prompts import PROMPTS_VERSION

    raw = RawAnalysis.model_validate({
        "results": [{"parameter": "Гемоглобин", "value": "145", "unit": "г/л"}],
    })
    object.__setattr__(raw, "_raw_response",
                       MagicMock(usage=MagicMock(prompt_tokens=10, completion_tokens=5)))
    fake = MagicMock()
    fake.chat.completions.create.return_value = raw

    with patch("botkin.llm.extract.get_client", return_value=fake), \
         patch("botkin.llm.extract.prepare_images", return_value=[b"\xff\xd8fakejpeg"]), \
         caplog.at_level(logging.INFO):
        extract.run_analysis(_tiny_pdf(tmp_path))

    assert any(PROMPTS_VERSION in r.getMessage() for r in caplog.records)


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


def test_extract_analysis_falls_back_to_harvester(tmp_path):
    """Если структурный разбор пуст (модель прислала чужую схему) — harvester по сырому JSON."""
    import json
    from botkin.llm import extract
    from botkin.llm.extract import RawAnalysis

    # Структурно RawAnalysis пуст (русские ключи не совпали со схемой), но сырой JSON есть.
    raw = RawAnalysis.model_validate({})
    ru_json = json.dumps({"": {"Исследование": "Клинический анализ крови", "Результат": [
        {"Исследование": "Гемоглобин", "Результат": "13.7 г/дл", "Единицы": "г/дл", "Референс": "11.7 - 15.5"},
        {"Исследование": "Базофилы, %", "Результат": "0.6%", "Единицы": "%", "Референс": "< 1.0"},
    ]}}, ensure_ascii=False)
    object.__setattr__(raw, "_raw_response", MagicMock(
        usage=MagicMock(prompt_tokens=10, completion_tokens=5),
        choices=[MagicMock(message=MagicMock(content=ru_json))],
    ))

    fake = MagicMock()
    fake.chat.completions.create.return_value = raw

    with patch("botkin.llm.extract.get_client", return_value=fake), \
         patch("botkin.llm.extract.prepare_images", return_value=[b"\xff\xd8fakejpeg"]):
        items = extract.run_analysis(_tiny_pdf(tmp_path))

    assert [i.analyte_name for i in items] == ["Гемоглобин", "Базофилы, %"]
    assert items[0].value_num == 13.7 and items[0].ref_low == 11.7
    assert items[1].ref_operator == "<" and items[1].ref_high == 1.0


def test_run_analysis_multipage_backfills_missing_page(tmp_path):
    """Гибрид: общий вызов потерял страницу (исследований < страниц) → добор постранично + дедуп."""
    import base64
    from botkin.llm import extract
    from botkin.domain.models import LabResult

    oak = [LabResult(analyte_name="Гематокрит", value_num=40.8, unit="%"),
           LabResult(analyte_name="Гемоглобин", value_num=13.7, unit="г/дл")]
    srb = [LabResult(analyte_name="С-реактивный белок", value_num=1.8, unit="мг/л")]
    page1 = base64.b64encode(b"PAGE1").decode()

    def fake_once(b64_images, doc_name):
        if len(b64_images) == 2:
            return list(oak), 1            # общий: только ОАК (1 исследование) < 2 страниц
        if b64_images == [page1]:
            return list(srb), 1            # стр.1 — СРБ
        return list(oak), 1                # стр.2 — снова ОАК (должен схлопнуться дедупом)

    with patch("botkin.llm.extract._extract_once", side_effect=fake_once), \
         patch("botkin.llm.extract.prepare_images", return_value=[b"PAGE1", b"PAGE2"]):
        items = extract.run_analysis(_tiny_pdf(tmp_path))

    names = [i.analyte_name for i in items]
    assert "С-реактивный белок" in names           # потерянная страница добрана
    assert "Гематокрит" in names and "Гемоглобин" in names
    assert names.count("Гематокрит") == 1          # дедуп: ОАК не задвоился


def test_run_analysis_singlepage_no_backfill(tmp_path):
    """Одна страница — постраничный добор не запускается (экономия вызовов)."""
    from botkin.llm import extract
    from botkin.domain.models import LabResult

    calls = []

    def fake_once(b64_images, doc_name):
        calls.append(len(b64_images))
        return [LabResult(analyte_name="Глюкоза", value_num=5.4, unit="ммоль/л")], 1

    with patch("botkin.llm.extract._extract_once", side_effect=fake_once), \
         patch("botkin.llm.extract.prepare_images", return_value=[b"PAGE1"]):
        items = extract.run_analysis(_tiny_pdf(tmp_path))

    assert len(items) == 1 and items[0].analyte_name == "Глюкоза"
    assert calls == [1]                            # ровно один вызов, без добора
