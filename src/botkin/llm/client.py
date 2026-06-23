"""Тонкая обёртка над Ollama через OpenAI-compatible интерфейс."""
import logging
import os
import platform
import subprocess
import urllib.request

from openai import OpenAI
from pydantic import BaseModel

import instructor
from botkin.config import (
    OLLAMA_URL, OLLAMA_KEEP_ALIVE, VLM_NUM_CTX, VLM_REPEAT_PENALTY, VLM_NUM_PREDICT,
    VLM_REQUEST_TIMEOUT, VLM_STRUCTURED_OUTPUT,
)

log = logging.getLogger(__name__)


def default_options() -> dict:
    """Опции Ollama для VLM-вызовов. keep_alive держит модель в VRAM между вызовами."""
    return {
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "num_ctx": VLM_NUM_CTX,
        "repeat_penalty": VLM_REPEAT_PENALTY,
        "num_predict": VLM_NUM_PREDICT,
    }


def build_extra_body(response_model: type[BaseModel], options: dict | None = None) -> dict:
    """extra_body для OpenAI-SDK → Ollama: опции + (опц.) нативный format=JSON-схема.

    Нативный параметр Ollama `format` принуждает грамматику декодера к схеме (XGrammar).
    Прокидываем его в обход instructor — instructor.Mode.JSON остаётся для валидации и
    ретраев, а схему на уровне токенов держит Ollama. OpenAI-стандартный
    response_format=json_schema на /v1 Ollama игнорирует (ollama/ollama#10001), поэтому
    именно нативный format. Под флагом VLM_STRUCTURED_OUTPUT — можно отключить.
    """
    body: dict = {"options": options or default_options()}
    if VLM_STRUCTURED_OUTPUT:
        body["format"] = response_model.model_json_schema()
    return body


def usage_of(response) -> tuple[int, int]:
    """(prompt_tokens, completion_tokens) из ответа instructor; (0, 0) если их нет.

    usage — приватное поле _raw_response и нужно только для лога. Раньше обращение к нему
    было незащищённым: успешный вызов падал, если usage недоступен (None/другой формат).
    """
    try:
        u = response._raw_response.usage
        return int(u.prompt_tokens), int(u.completion_tokens)
    except (AttributeError, TypeError, ValueError):
        return 0, 0

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


def get_raw_client(timeout: float | None = None) -> OpenAI:
    return OpenAI(
        base_url=f"{_detect_ollama_url()}/v1",
        api_key="ollama",
        timeout=VLM_REQUEST_TIMEOUT if timeout is None else timeout,
    )


def get_client(temperature: float = 0.1, mode: instructor.Mode = instructor.Mode.JSON):
    return instructor.from_openai(get_raw_client(), mode=mode)