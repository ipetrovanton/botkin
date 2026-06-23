"""P2-гигиена client.py: таймауты-магические-числа → config, провязка в пробу."""
import inspect

from botkin import config
from botkin.llm import client


def test_probe_and_wsl_timeouts_exported_from_config():
    assert config.OLLAMA_PROBE_TIMEOUT > 0
    assert config.OLLAMA_WSL_DETECT_TIMEOUT > 0


def test_reachable_probe_default_timeout_from_config():
    sig = inspect.signature(client._is_url_reachable)
    assert sig.parameters["timeout"].default == config.OLLAMA_PROBE_TIMEOUT


def test_unreachable_url_returns_false():
    # порт заведомо закрыт — проба должна тихо вернуть False, не пробросив исключение.
    assert client._is_url_reachable("http://127.0.0.1:1") is False
