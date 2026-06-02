"""Тонкая обёртка над Ollama через OpenAI-compatible интерфейс."""
import logging
import os
import platform
import subprocess
import urllib.request

from openai import OpenAI

import instructor
from botkin.config import OLLAMA_URL

log = logging.getLogger(__name__)

_ollama_url: str | None = None


def _is_url_reachable(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/version", timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _detect_ollama_url() -> str:
    global _ollama_url
    if _ollama_url is not None:
        return _ollama_url

    url = os.getenv("OLLAMA_URL") or OLLAMA_URL
    if _is_url_reachable(url):
        _ollama_url = url
        return url

    if platform.system() == "Windows":
        try:
            output = subprocess.check_output(
                ["wsl", "-d", "Ubuntu", "hostname", "-I"],
                shell=False, timeout=5,
            ).decode().strip()
            ip = output.split()[0] if output else None
            if ip:
                candidate = f"http://{ip}:11434"
                if _is_url_reachable(candidate):
                    _ollama_url = candidate
                    return candidate
        except Exception:
            pass

    _ollama_url = url
    return url


def get_raw_client(timeout: float = 600.0) -> OpenAI:
    return OpenAI(
        base_url=f"{_detect_ollama_url()}/v1",
        api_key="ollama",
        timeout=timeout,
    )


def get_client(temperature: float = 0.1, mode: instructor.Mode = instructor.Mode.JSON):
    return instructor.from_openai(get_raw_client(), mode=mode)