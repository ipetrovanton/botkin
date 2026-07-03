"""P2-гигиена client.py: таймауты-магические-числа → config, провязка в пробу."""
import inspect
import json

from botkin import config
from botkin.llm import client


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_probe_and_wsl_timeouts_exported_from_config():
    assert config.OLLAMA_PROBE_TIMEOUT > 0
    assert config.OLLAMA_WSL_DETECT_TIMEOUT > 0


def test_reachable_probe_default_timeout_from_config():
    sig = inspect.signature(client._is_url_reachable)
    assert sig.parameters["timeout"].default == config.OLLAMA_PROBE_TIMEOUT


def test_unreachable_url_returns_false():
    # порт заведомо закрыт — проба должна тихо вернуть False, не пробросив исключение.
    assert client._is_url_reachable("http://127.0.0.1:1") is False


def test_warmup_posts_native_load_request(monkeypatch):
    # Прогрев грузит модель через /api/generate с num_ctx=VLM_NUM_CTX и keep_alive,
    # чтобы ранер совпал с боевыми /v1-вызовами (иначе Ollama перезагрузит модель).
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode())
        return _FakeResp()

    monkeypatch.setattr(client, "_detect_ollama_url", lambda: "http://x:11434")
    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)

    client.warmup(["qwen3-vl:8b-instruct"])

    assert captured["url"].endswith("/api/generate")
    assert captured["body"]["model"] == "qwen3-vl:8b-instruct"
    assert captured["body"]["keep_alive"] == config.OLLAMA_KEEP_ALIVE
    assert captured["body"]["options"]["num_ctx"] == config.VLM_NUM_CTX
    assert captured["timeout"] == config.OLLAMA_WARMUP_TIMEOUT


def test_warmup_is_best_effort_when_ollama_down(monkeypatch):
    # Ollama недоступна → прогрев логирует и молча выходит, не роняя старт сервиса.
    def boom(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(client, "_detect_ollama_url", lambda: "http://x:11434")
    monkeypatch.setattr(client.urllib.request, "urlopen", boom)

    client.warmup(["m"])  # не должно бросить исключение


def test_warmup_models_dedup_when_text_equals_vlm(monkeypatch):
    monkeypatch.setattr(client, "VLM_MODEL", "qwen3-vl:8b-instruct")
    monkeypatch.setattr(client, "TEXT_MODEL", "qwen3-vl:8b-instruct")
    assert client._warmup_models() == ["qwen3-vl:8b-instruct"]

    monkeypatch.setattr(client, "TEXT_MODEL", "qwen3:8b")
    assert client._warmup_models() == ["qwen3-vl:8b-instruct", "qwen3:8b"]
