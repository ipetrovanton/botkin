"""Timing utility for LLM calls — replaces 10+ manual time.perf_counter() patterns."""

import logging
import time
from contextlib import contextmanager
from typing import Generator

from botkin.llm.metrics import InferenceMetrics, log_metrics

log = logging.getLogger(__name__)


@contextmanager
def timed(label: str, doc_name: str = "") -> Generator[dict, None, None]:
    """Context manager that logs elapsed time for an operation.

    Usage:
        with timed("EXTRACT", doc_name) as t:
            t["metrics"] = metrics_of(...)
        # t["elapsed"] contains the duration

    The yielded dict contains "elapsed" (float seconds) and optional "metrics".
    """
    t0 = time.perf_counter()
    ctx: dict = {"elapsed": 0.0, "metrics": None}
    try:
        yield ctx
    finally:
        ctx["elapsed"] = time.perf_counter() - t0
        prefix = f"[{label}]"
        if doc_name:
            prefix = f"[{label}] Doc: '{doc_name}'"

        metrics: InferenceMetrics | None = ctx.get("metrics")
        if metrics is not None:
            log_metrics(metrics, doc_name=doc_name)
        else:
            log.info("%s | Elapsed: %.2fs", prefix, ctx["elapsed"])
