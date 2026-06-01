"""Тонкая обёртка над Ollama через OpenAI-compatible interface."""
import os
import platform
import subprocess
import logging
import urllib.request
from openai import OpenAI
import instructor

from backend.config import VLM_MODEL

log = logging.getLogger(__name__)

_OLLAMA_URL: str | None = None
LLM_MODEL = VLM_MODEL


def _is_url_reachable(url: str, timeout: float = 1.5) -> bool:
    probe = f"{url}/api/version"
    try:
        with urllib.request.urlopen(probe, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _get_ollama_url() -> str:
    """Определяет URL для подключения к Ollama (с кэшированием)."""
    global _OLLAMA_URL
    if _OLLAMA_URL is not None:
        return _OLLAMA_URL

    default_url = "http://localhost:11434"

    if os.getenv("OLLAMA_URL"):
        _OLLAMA_URL = os.getenv("OLLAMA_URL")
        return _OLLAMA_URL

    if platform.system() == "Windows":
        try:
            output = subprocess.check_output(
                ["wsl", "-d", "Ubuntu", "hostname", "-I"],
                shell=False,
                timeout=5,
            ).decode().strip()
            ip = output.split()[0] if output else None
            if ip:
                candidate = f"http://{ip}:11434"
                if _is_url_reachable(candidate):
                    _OLLAMA_URL = candidate
                    return _OLLAMA_URL
        except Exception:
            pass

    _OLLAMA_URL = default_url
    return _OLLAMA_URL


def get_raw_client(timeout: float = 600.0) -> OpenAI:
    return OpenAI(
        base_url=f"{_get_ollama_url()}/v1",
        api_key="ollama",
        timeout=timeout,
    )


def get_client(temperature: float = 0.1, mode: instructor.Mode = instructor.Mode.JSON):
    """Возвращает instructor-патченный клиент."""
    raw_client = get_raw_client()
    return instructor.from_openai(raw_client, mode=mode)


def chat_completion(
    messages: list[dict], temperature: float = 0.1, max_tokens: int = 2048
) -> str:
    """Простой chat без structured output."""
    raw = OpenAI(base_url=f"{_get_ollama_url()}/v1", api_key="ollama")
    resp = raw.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""