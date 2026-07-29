"""Обёртка над LLM inference-бэкендами через OpenAI-compatible интерфейс.

Поддерживает три бэкенда (выбор через LLM_BACKEND env):
- ollama (по умолчанию): XGrammar structured output, keep_alive, warmup
- vllm: guided_json (outlines), модель всегда в VRAM
- mlx: instructor-only валидация, модель в unified memory
"""
import json
import logging
import os
import platform
import subprocess
import time
import urllib.request
from json import JSONDecodeError

from openai import OpenAI
from pydantic import BaseModel, ValidationError
from tenacity import (
    Retrying, retry_if_exception_type, stop_after_attempt, stop_after_delay,
    wait_exponential_jitter,
)

import instructor
from botkin.config import (
    OLLAMA_URL, OLLAMA_KEEP_ALIVE, OLLAMA_PROBE_TIMEOUT, OLLAMA_WSL_DETECT_TIMEOUT,
    OLLAMA_WARMUP_TIMEOUT, VLM_MODEL, TEXT_MODEL,
    VLM_NUM_CTX, VLM_REPEAT_PENALTY, VLM_NUM_PREDICT,
    VLM_MAX_RETRIES, VLM_REQUEST_TIMEOUT, VLM_RETRY_INITIAL_WAIT, VLM_RETRY_MAX_SECONDS,
    VLM_RETRY_MAX_WAIT, VLM_STRUCTURED_OUTPUT,
)

log = logging.getLogger(__name__)


def build_retrying(
    initial_wait: float | None = None,
    max_wait: float | None = None,
    attempts: int | None = None,
    max_seconds: float | None = None,
) -> Retrying:
    """tenacity-Retrying для VLM-вызовов: экспоненциальный backoff с джиттером.

    Ретраим только ошибки парсинга/валидации ответа (JSONDecodeError, ValidationError) —
    модель «недоген» JSON, повтор имеет смысл. Битый запрос (4xx, слишком большое
    изображение и т.п.) не ретраим: повтор его не починит, лишь жжёт время и GPU.
    Стоп — по числу попыток И по суммарному времени (деградировавшая модель не крутит
    ретраи минутами); джиттер разводит одновременные повторы к Ollama.

    reraise=False (дефолт) — намеренно: instructor ожидает на исчерпании RetryError,
    который он перехватывает и оборачивает в InstructorRetryException с last_completion.
    Этот last_completion нужен salvage обрезанного JSON (_raw_text_from_exc). При
    reraise=True наружу летел бы голый ValidationError мимо перехвата — salvage сломался бы.
    """
    initial = VLM_RETRY_INITIAL_WAIT if initial_wait is None else initial_wait
    mx = VLM_RETRY_MAX_WAIT if max_wait is None else max_wait
    n = (VLM_MAX_RETRIES + 1) if attempts is None else attempts
    secs = VLM_RETRY_MAX_SECONDS if max_seconds is None else max_seconds
    return Retrying(
        retry=retry_if_exception_type((JSONDecodeError, ValidationError)),
        wait=wait_exponential_jitter(initial=initial, max=mx),
        stop=stop_after_attempt(n) | stop_after_delay(secs),
    )


def get_backend() -> str:
    """Текущий inference-бэкенд: ollama | vllm | mlx."""
    return os.getenv("LLM_BACKEND", "ollama").lower()


_MODEL_MAP = {
    "vllm": {
        "qwen3-vl:8b-instruct": "Qwen/Qwen3-VL-8B-Instruct",
        "qwen3:8b": "Qwen/Qwen3-8B",
    },
    "mlx": {
        "qwen3-vl:8b-instruct": "mlx-community/Qwen3-VL-8B-Instruct-4bit",
        "qwen3:8b": "mlx-community/Qwen3-8B-4bit",
    },
}


def model_name(model: str) -> str:
    """Преобразует имя модели в формат, ожидаемый текущим бэкендом."""
    backend = get_backend()
    return _MODEL_MAP.get(backend, {}).get(model, model)


def default_options() -> dict:
    """Опции для VLM-вызовов. Для Ollama — keep_alive и параметры контекста.
    Для vLLM/MLX — пустой словарь (параметры задаются при запуске сервера)."""
    if get_backend() != "ollama":
        return {}
    return {
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "num_ctx": VLM_NUM_CTX,
        "repeat_penalty": VLM_REPEAT_PENALTY,
        "num_predict": VLM_NUM_PREDICT,
    }


def ocr_options() -> dict:
    """Опции для OCR-модели первой ступени."""
    from botkin.config import (
        OCR_NUM_CTX, OCR_NUM_PREDICT, OCR_REPEAT_PENALTY, OLLAMA_KEEP_ALIVE,
    )
    if get_backend() != "ollama":
        return {}
    return {
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "num_ctx": OCR_NUM_CTX,
        "repeat_penalty": OCR_REPEAT_PENALTY,
        "num_predict": OCR_NUM_PREDICT,
    }


def build_extra_body(
    response_model: type[BaseModel],
    options: dict | None = None,
    structured: bool | None = None,
) -> dict:
    """extra_body для OpenAI-SDK, зависящий от бэкенда.

    Ollama: нативный format=JSON-схема (XGrammar) + options.
    vLLM: guided_json (outlines) для structured output.
    MLX: нет native grammar — instructor handles validation + retry.
    """
    backend = get_backend()
    use_format = VLM_STRUCTURED_OUTPUT if structured is None else structured
    body: dict = {}

    if backend == "ollama":
        body["options"] = options or default_options()
        if use_format:
            body["format"] = response_model.model_json_schema()
    elif backend == "vllm":
        if use_format:
            body["guided_json"] = response_model.model_json_schema()
    # mlx: no native grammar constraint; instructor handles validation + retry

    if os.getenv("VLM_DISABLE_THINKING", "").lower() in ("1", "true", "yes", "on"):
        body["chat_template_kwargs"] = {"enable_thinking": False}
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


def _is_url_reachable(url: str, timeout: float = OLLAMA_PROBE_TIMEOUT) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/version", timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (OSError, ValueError):
        # OSError покрывает URLError/socket.timeout/отказ соединения; ValueError — кривой URL.
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
                shell=False, timeout=OLLAMA_WSL_DETECT_TIMEOUT,
            ).decode().strip()
            ip = output.split()[0] if output else None
            if ip:
                candidate = f"http://{ip}:11434"
                if _is_url_reachable(candidate):
                    _ollama_url = candidate
                    return candidate
        except (OSError, subprocess.SubprocessError) as e:
            # Детект WSL-IP — необязательный путь; падение не критично, но логируем.
            log.debug("WSL-детект Ollama не удался: %s", e)

    _ollama_url = url
    return url


def get_raw_client(timeout: float | None = None) -> OpenAI:
    """OpenAI-совместимый клиент для текущего бэкенда."""
    backend = get_backend()
    t = VLM_REQUEST_TIMEOUT if timeout is None else timeout
    if backend == "vllm":
        url = os.getenv("VLLM_URL", "http://localhost:8001")
        return OpenAI(base_url=f"{url}/v1", api_key="vllm", timeout=t)
    if backend == "mlx":
        url = os.getenv("MLX_URL", "http://localhost:8002")
        return OpenAI(base_url=f"{url}/v1", api_key="mlx", timeout=t)
    # default: ollama
    return OpenAI(
        base_url=f"{_detect_ollama_url()}/v1",
        api_key="ollama",
        timeout=t,
    )


def get_client(mode: instructor.Mode = instructor.Mode.JSON):
    # Температура задаётся per-request через extra_body/options, не при создании клиента.
    return instructor.from_openai(get_raw_client(), mode=mode)


def _warmup_models() -> list[str]:
    """Модели для прогрева: VLM + text (без дублей, с сохранением порядка)."""
    seen: dict[str, None] = {}
    for m in (VLM_MODEL, TEXT_MODEL):
        seen.setdefault(m, None)
    return list(seen)


def warmup(models: list[str] | None = None) -> None:
    """Загрузить веса модели(ей) в VRAM заранее.

    Для Ollama — нативный /api/generate с пустым prompt.
    Для vLLM/MLX — no-op: модель уже загружена при старте сервера.
    """
    if get_backend() != "ollama":
        log.info("[WARMUP] backend=%s — прогрев не нужен, пропуск", get_backend())
        return
    url = _detect_ollama_url()
    for model in (models or _warmup_models()):
        payload = json.dumps({
            "model": model,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": {"num_ctx": VLM_NUM_CTX},
        }).encode()
        req = urllib.request.Request(
            f"{url}/api/generate", data=payload,
            headers={"Content-Type": "application/json"},
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=OLLAMA_WARMUP_TIMEOUT):
                pass
            log.info("[WARMUP] '%s' загружена в VRAM за %.1fs", model, time.perf_counter() - t0)
        except (OSError, ValueError) as e:
            log.warning("[WARMUP] '%s' не прогрета (Ollama недоступна?): %s", model, e)