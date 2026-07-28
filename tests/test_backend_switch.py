"""Tests for configurable LLM backend switching (Ollama / vLLM / MLX)."""

import os

import pytest
from pydantic import BaseModel

from botkin.llm.client import (
    build_extra_body,
    default_options,
    get_backend,
    get_raw_client,
    model_name,
    warmup,
)


class _DummyModel(BaseModel):
    name: str
    value: int


class TestBackendSelection:
    """get_backend() reads LLM_BACKEND env var."""

    def test_default_is_ollama(self, monkeypatch):
        monkeypatch.delenv("LLM_BACKEND", raising=False)
        assert get_backend() == "ollama"

    def test_vllm(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "vllm")
        assert get_backend() == "vllm"

    def test_mlx(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "mlx")
        assert get_backend() == "mlx"

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "VLLM")
        assert get_backend() == "vllm"


class TestModelNameMapping:
    """model_name() translates model IDs per backend."""

    def test_ollama_passthrough(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "ollama")
        assert model_name("qwen3-vl:8b-instruct") == "qwen3-vl:8b-instruct"

    def test_vllm_mapping(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "vllm")
        assert model_name("qwen3-vl:8b-instruct") == "Qwen/Qwen3-VL-8B-Instruct"

    def test_mlx_mapping(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "mlx")
        assert model_name("qwen3-vl:8b-instruct") == "mlx-community/Qwen3-VL-8B-Instruct-4bit"

    def test_unknown_model_passthrough(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "vllm")
        assert model_name("some-custom-model") == "some-custom-model"


class TestDefaultOptions:
    """default_options() returns Ollama-specific options only for ollama backend."""

    def test_ollama_returns_options(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "ollama")
        opts = default_options()
        assert "keep_alive" in opts
        assert "num_ctx" in opts

    def test_vllm_returns_empty(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "vllm")
        assert default_options() == {}

    def test_mlx_returns_empty(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "mlx")
        assert default_options() == {}


class TestBuildExtraBody:
    """build_extra_body() uses backend-specific structured output mechanism."""

    def test_ollama_uses_format(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "ollama")
        monkeypatch.setenv("VLM_STRUCTURED_OUTPUT", "true")
        body = build_extra_body(_DummyModel)
        assert "format" in body
        assert "options" in body

    def test_vllm_uses_guided_json(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "vllm")
        body = build_extra_body(_DummyModel)
        assert "guided_json" in body
        assert "format" not in body

    def test_mlx_no_grammar(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "mlx")
        body = build_extra_body(_DummyModel)
        assert "format" not in body
        assert "guided_json" not in body

    def test_ollama_structured_off(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "ollama")
        body = build_extra_body(_DummyModel, structured=False)
        assert "format" not in body


class TestWarmup:
    """warmup() is a no-op for non-Ollama backends."""

    def test_vllm_skips_warmup(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "vllm")
        # Should not raise even though no vLLM server is running
        warmup()

    def test_mlx_skips_warmup(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "mlx")
        warmup()


class TestGetRawClient:
    """get_raw_client() returns OpenAI client with correct base_url."""

    def test_vllm_url(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "vllm")
        monkeypatch.setenv("VLLM_URL", "http://localhost:8001")
        client = get_raw_client()
        assert "localhost:8001" in str(client.base_url)

    def test_mlx_url(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "mlx")
        monkeypatch.setenv("MLX_URL", "http://localhost:8002")
        client = get_raw_client()
        assert "localhost:8002" in str(client.base_url)
