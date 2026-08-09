"""Очередь VLM: позиция документа видна, порядок FIFO, слот освобождается при ошибке."""
import asyncio

import pytest

from botkin.pipeline.queue import LlmQueue


@pytest.mark.asyncio
async def test_position_zero_while_active():
    q = LlmQueue()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def work():
        async with q.slot(1):
            entered.set()
            await release.wait()

    task = asyncio.create_task(work())
    await entered.wait()
    assert q.position(1) == 0
    assert q.snapshot() == {"active": 1, "waiting": 0}
    release.set()
    await task
    assert q.position(1) is None
    assert q.snapshot() == {"active": 0, "waiting": 0}


@pytest.mark.asyncio
async def test_fifo_positions_while_waiting():
    q = LlmQueue()
    first_in = asyncio.Event()
    release = asyncio.Event()
    order: list[int] = []

    async def work(doc_id: int, gate: asyncio.Event | None = None):
        async with q.slot(doc_id):
            if gate:
                gate.set()
                await release.wait()
            order.append(doc_id)

    t1 = asyncio.create_task(work(1, first_in))
    await first_in.wait()
    t2 = asyncio.create_task(work(2))
    t3 = asyncio.create_task(work(3))
    await asyncio.sleep(0)  # даём задачам встать в очередь

    assert q.position(1) == 0
    assert q.position(2) == 1
    assert q.position(3) == 2
    assert q.position(99) is None
    assert q.snapshot() == {"active": 1, "waiting": 2}

    release.set()
    await asyncio.gather(t1, t2, t3)
    assert order == [1, 2, 3]


@pytest.mark.asyncio
async def test_slot_released_on_exception():
    q = LlmQueue()
    with pytest.raises(RuntimeError):
        async with q.slot(1):
            raise RuntimeError("boom")
    assert q.position(1) is None
    assert q.snapshot() == {"active": 0, "waiting": 0}


@pytest.mark.asyncio
async def test_cancelled_waiter_leaves_queue():
    q = LlmQueue()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold():
        async with q.slot(1):
            entered.set()
            await release.wait()

    async def waiter():
        async with q.slot(2):
            pass

    t1 = asyncio.create_task(hold())
    await entered.wait()
    t2 = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    assert q.position(2) == 1
    t2.cancel()
    await asyncio.gather(t2, return_exceptions=True)
    assert q.position(2) is None
    release.set()
    await t1
