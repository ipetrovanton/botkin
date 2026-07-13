"""Тонкая обёртка над Ollama через OpenAI-compatible интерфейс."""
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


def default_options() -> dict:
    """Опции Ollama для VLM-вызовов. keep_alive держит модель в VRAM между вызовами."""
    return {
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "num_ctx": VLM_NUM_CTX,
        "repeat_penalty": VLM_REPEAT_PENALTY,
        "num_predict": VLM_NUM_PREDICT,
    }


def build_extra_body(
    response_model: type[BaseModel],
    options: dict | None = None,
    structured: bool | None = None,
) -> dict:
    """extra_body для OpenAI-SDK → Ollama: опции + (опц.) нативный format=JSON-схема.

    Нативный параметр Ollama `format` принуждает грамматику декодера к схеме (XGrammar).
    Прокидываем его в обход instructor — instructor.Mode.JSON остаётся для валидации и
    ретраев, а схему на уровне токенов держит Ollama. OpenAI-стандартный
    response_format=json_schema на /v1 Ollama игнорирует (ollama/ollama#10001), поэтому
    именно нативный format. Под флагом VLM_STRUCTURED_OUTPUT — можно отключить.

    structured=None → использовать глобальный VLM_STRUCTURED_OUTPUT; True/False —
    форсировать включение/выключение (для адаптивного фолбэка: XGrammar на сложных
    картинках сваливается в пустой, но валидный по схеме объект — тогда повторяем без format).
    """
    use_format = VLM_STRUCTURED_OUTPUT if structured is None else structured
    body: dict = {"options": options or default_options()}
    if use_format:
        body["format"] = response_model.model_json_schema()
    # Qwen3.5+ имеет thinking mode включённый по умолчанию; для vision/OCR задач
    # весь вывод уходит в thinking field, content остаётся пустым (ollama/ollama#14502).
    # Отключаем через chat_template_kwargs, когда env VLM_DISABLE_THINKING=1.
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
    return OpenAI(
        base_url=f"{_detect_ollama_url()}/v1",
        api_key="ollama",
        timeout=VLM_REQUEST_TIMEOUT if timeout is None else timeout,
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
    """Загрузить веса модели(ей) в VRAM заранее — чтобы первый документ не платил
    холодный старт (~100–120s на 6 ГБ). Best-effort: Ollama недоступна → лог, не падаем.

    Грузим нативным Ollama `/api/generate` с пустым prompt: он лишь загружает модель
    (`done_reason: "load"`) и не генерирует. Прокидываем num_ctx=VLM_NUM_CTX и keep_alive,
    чтобы прогретый ранер совпал по параметрам с боевыми /v1-вызовами (иначе Ollama
    перезагрузит модель при первом реальном запросе).
    """
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