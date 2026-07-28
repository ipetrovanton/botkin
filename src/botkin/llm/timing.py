"""Timing utility for LLM calls — replaces 10+ manual time.perf_counter() patterns."""

import logging
import time
from contextlib import contextmanager
from typing import Generator

log = logging.getLogger(__name__)


@contextmanager
def timed(label: str, doc_name: str = "") -> Generator[dict, None, None]:
    """Context manager that logs elapsed time for an operation.

    Usage:
        with timed("EXTRACT", doc_name) as t:
            result = do_work()
        # t["elapsed"] contains the duration

    The yielded dict is populated with "elapsed" (float seconds) on exit.
    """
    t0 = time.perf_counter()
    ctx: dict = {"elapsed": 0.0}
    try:
        yield ctx
    finally:
        ctx["elapsed"] = time.perf_counter() - t0
        prefix = f"[{label}]"
        if doc_name:
            prefix = f"[{label}] Doc: '{doc_name}'"
        log.info("%s | Elapsed: %.2fs", prefix, ctx["elapsed"])
