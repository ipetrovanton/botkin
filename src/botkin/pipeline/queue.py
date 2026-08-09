"""FIFO-очередь VLM-вызовов с видимой позицией.

Обработка сериализована (одна GPU — параллельные VLM-вызовы только вредят),
но при нескольких активных пользователях документы ждут молча. Очередь даёт
позицию документа («вы третий»), чтобы фронт и бот показывали её пользователю.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class LlmQueue:
    """Семафор + учёт порядка ожидания. Позиция: 0 — обрабатывается, N≥1 — в очереди."""

    def __init__(self, concurrency: int = 1) -> None:
        self._sem = asyncio.Semaphore(concurrency)
        self._waiting: list[int] = []
        self._active: set[int] = set()

    def position(self, document_id: int) -> int | None:
        """1-based позиция ожидания; 0 — выполняется; None — не в очереди."""
        if document_id in self._active:
            return 0
        try:
            return self._waiting.index(document_id) + 1
        except ValueError:
            return None

    def snapshot(self) -> dict:
        return {"active": len(self._active), "waiting": len(self._waiting)}

    @asynccontextmanager
    async def slot(self, document_id: int):
        """Занять слот VLM: регистрирует ожидание, внутри контекста — обработку."""
        self._waiting.append(document_id)
        try:
            async with self._sem:
                self._waiting.remove(document_id)
                self._active.add(document_id)
                try:
                    yield
                finally:
                    self._active.discard(document_id)
        finally:
            if document_id in self._waiting:
                self._waiting.remove(document_id)


LLM_QUEUE = LlmQueue()
