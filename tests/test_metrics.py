"""Юнит-тесты для метрик инференса (botkin.llm.metrics) и timing."""
import logging
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from botkin.llm.metrics import InferenceMetrics, metrics_of
from botkin.llm.timing import timed


class FakeUsage:
    prompt_tokens = 1234
    completion_tokens = 567


class FakeOllamaUsage:
    prompt_tokens = 100
    completion_tokens = 50
    prompt_eval_count = 100
    eval_count = 50
    eval_duration = 1_234_567_890  # ns
    total_duration = 2_000_000_000  # ns


def test_metrics_of_extracts_openai_usage():
    """metrics_of извлекает prompt/completion tokens из OpenAI-совместимого ответа."""
    response = MagicMock()
    response._raw_response.usage = FakeUsage()

    m = metrics_of(response, "qwen3-vl:8b-instruct", 13.8, num_ctx=8192)

    assert m.model == "qwen3-vl:8b-instruct"
    assert m.prompt_tokens == 1234
    assert m.completion_tokens == 567
    assert m.num_ctx == 8192
    assert m.elapsed_s == 13.8
    assert m.tokens_per_second == pytest.approx(567 / 13.8, rel=1e-3)


def test_metrics_of_handles_missing_usage():
    """Если usage недоступен — метрики не падают, tokens 0, tps None."""
    response = MagicMock()
    response._raw_response.usage = None

    m = metrics_of(response, "qwen3:8b", 2.0, num_ctx=4096)

    assert m.prompt_tokens == 0
    assert m.completion_tokens == 0
    assert m.tokens_per_second is None


def test_metrics_of_ollama_extra_fields():
    """Дополнительные Ollama-поля (eval_count, eval_duration, total_duration) сохраняются."""
    response = MagicMock()
    response._raw_response.usage = FakeOllamaUsage()

    m = metrics_of(response, "qwen3:8b", 1.5, num_ctx=16384)

    assert m.prompt_eval_count == 100
    assert m.eval_count == 50
    assert m.eval_duration_ns == 1_234_567_890
    assert m.total_duration_ns == 2_000_000_000
    assert m.tokens_per_second == pytest.approx(50 / 1.5, rel=1e-3)


def test_metrics_of_falls_back_to_top_level_usage():
    """Если ответ без _raw_response (raw OpenAI client), читаем .usage напрямую."""
    response = MagicMock()
    response.usage = FakeUsage()
    response._raw_response = None

    m = metrics_of(response, "qwen3-vl:8b-instruct", 1.0)

    assert m.prompt_tokens == 1234
    assert m.completion_tokens == 567


def test_inference_metrics_serializable():
    """Dataclass можно превратить в dict (для записи в benchmarks/)."""
    m = InferenceMetrics(
        model="qwen3:8b", prompt_tokens=10, completion_tokens=5,
        elapsed_s=1.2, num_ctx=8192, tokens_per_second=4.17,
    )
    d = asdict(m)
    assert d["model"] == "qwen3:8b"
    assert d["prompt_tokens"] == 10
    assert d["num_ctx"] == 8192


def test_timed_logs_elapsed_only_without_metrics(caplog):
    """timed() без установленных metrics логирует elapsed."""
    with patch("botkin.llm.timing.log") as mock_log:
        with timed("TEST", "doc") as t:
            t["foo"] = 1

    assert mock_log.info.called
    assert "Elapsed" in mock_log.info.call_args[0][0]


def test_timed_logs_metrics_when_provided(caplog):
    """timed() публикует [METRICS], если клиент заполнил ctx['metrics']."""
    with caplog.at_level(logging.INFO):
        with timed("TEXT_EXTRACT", "sample.pdf") as t:
            t["metrics"] = InferenceMetrics(
                model="qwen3:8b",
                prompt_tokens=1234,
                completion_tokens=567,
                elapsed_s=13.8,
                num_ctx=8192,
                tokens_per_second=41.0,
            )

    assert any("[METRICS]" in r.getMessage() for r in caplog.records)
    assert any("model=qwen3:8b" in r.getMessage() for r in caplog.records)
