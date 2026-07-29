"""Метрики инференса: извлечение usage-данных из ответа и форматирование для логов.

Ollama в OpenAI-совместимом ответе отдаёт usage.prompt_tokens / usage.completion_tokens,
а в нативном ответе — prompt_eval_count, eval_count, eval_duration, total_duration.
Обёртки (instructor/openai) дают доступ к usage через response._raw_response.usage.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InferenceMetrics:
    """Единый снимок метрик одного LLM-вызова."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    elapsed_s: float
    num_ctx: int | None = None
    tokens_per_second: float | None = None
    prompt_eval_count: int | None = None
    eval_count: int | None = None
    eval_duration_ns: int | None = None
    total_duration_ns: int | None = None


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _usage_from_response(response) -> tuple[int, int]:
    """(prompt_tokens, completion_tokens) с учётом обёрток и неполного usage."""
    try:
        raw = getattr(response, "_raw_response", None) or response
        usage = getattr(raw, "usage", None)
        if usage is None:
            return 0, 0
        return _to_int(getattr(usage, "prompt_tokens", None), 0), _to_int(getattr(usage, "completion_tokens", None), 0)
    except (AttributeError, TypeError, ValueError):
        return 0, 0


def _ollama_duration_ns(usage) -> int | None:
    """Ollama-специфичное поле eval_duration (ns) из usage-like объекта, если оно есть."""
    try:
        return _to_int(getattr(usage, "eval_duration", None), 0) or None
    except (AttributeError, TypeError, ValueError):
        return None


def _ollama_total_duration_ns(usage) -> int | None:
    try:
        return _to_int(getattr(usage, "total_duration", None), 0) or None
    except (AttributeError, TypeError, ValueError):
        return None


def _ollama_prompt_eval_count(usage) -> int | None:
    try:
        return _to_int(getattr(usage, "prompt_eval_count", None), 0) or None
    except (AttributeError, TypeError, ValueError):
        return None


def _ollama_eval_count(usage) -> int | None:
    try:
        return _to_int(getattr(usage, "eval_count", None), 0) or None
    except (AttributeError, TypeError, ValueError):
        return None


def _tokens_per_second(prompt_tokens: int, completion_tokens: int, elapsed_s: float) -> float | None:
    """tps по завершённым токенам; None, если время не считалось."""
    if elapsed_s <= 0 or completion_tokens == 0:
        return None
    return round(completion_tokens / elapsed_s, 2)


def metrics_of(
    response,
    model: str,
    elapsed_s: float,
    *,
    num_ctx: int | None = None,
) -> InferenceMetrics:
    """Построить InferenceMetrics из ответа модели и измеренного времени.

    response — объект, возвращённый instructor/openai (с _raw_response.usage) или
    прямой ответ OpenAI-совместимого клиента (с .usage).
    """
    prompt_tokens, completion_tokens = _usage_from_response(response)

    # Если usage-объект от Ollama, там могут быть дополнительные поля — читаем из raw usage.
    raw = getattr(response, "_raw_response", None) or response
    usage = getattr(raw, "usage", None)

    return InferenceMetrics(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        elapsed_s=round(elapsed_s, 3),
        num_ctx=num_ctx,
        tokens_per_second=_tokens_per_second(prompt_tokens, completion_tokens, elapsed_s),
        prompt_eval_count=_ollama_prompt_eval_count(usage),
        eval_count=_ollama_eval_count(usage),
        eval_duration_ns=_ollama_duration_ns(usage),
        total_duration_ns=_ollama_total_duration_ns(usage),
    )


def log_metrics(metrics: InferenceMetrics, *, doc_name: str = "") -> None:
    """Строчный лог формата '[METRICS] model=... ctx=... out=... tps=... t=...'."""
    ctx = f"{metrics.prompt_tokens}/{metrics.num_ctx}" if metrics.num_ctx else str(metrics.prompt_tokens)
    out = metrics.completion_tokens or (metrics.eval_count or 0)
    tps = f"{metrics.tokens_per_second:.1f}" if metrics.tokens_per_second is not None else "n/a"
    prefix = f"[METRICS] Doc: '{doc_name}'" if doc_name else "[METRICS]"
    log.info(
        "%s model=%s | ctx=%s | out=%d | tps=%s | t=%.2fs",
        prefix, metrics.model, ctx, out, tps, metrics.elapsed_s,
    )
