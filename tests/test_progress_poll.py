import asyncio

import botkin.bot.handlers.upload as up
from botkin.bot.progress import poll_until_done
from botkin.config import BOT_PROGRESS_TIMEOUT


def _clock(values):
    it = iter(values)
    return lambda: next(it)


def test_poll_edits_only_on_change_and_returns_final():
    statuses = iter(["recognizing", "recognizing", "normalizing", "extracted"])
    edits = []

    async def fake_status():
        return next(statuses)

    async def fake_edit(text):
        edits.append(text)

    async def fast_sleep(_):
        return None

    final = asyncio.run(poll_until_done(
        doc_id=9, get_status=fake_status, edit=fake_edit,
        sleep=fast_sleep, interval=0.0, timeout=100.0, now=_clock([0, 1, 2, 3, 4]),
    ))
    assert final == "extracted"
    # редактируем на смене НЕтерминальных стадий: recognizing, normalizing
    # (терминальный extracted не рисуем — финальную карточку рисует вызывающий код)
    assert len(edits) == 2


def test_poll_timeout_returns_none():
    async def fake_status():
        return "recognizing"

    async def fake_edit(text):
        return None

    async def fast_sleep(_):
        return None

    final = asyncio.run(poll_until_done(
        doc_id=1, get_status=fake_status, edit=fake_edit,
        sleep=fast_sleep, interval=0.0, timeout=5.0, now=_clock([0, 2, 4, 6]),
    ))
    assert final is None


def test_progress_timeout_covers_multipage_ceiling():
    # Потолок UI-поллинга должен покрывать classify + несколько VLM-вызовов
    # (общий + добор страниц), иначе бот сдаётся раньше бэкенда.
    assert BOT_PROGRESS_TIMEOUT >= 300


def test_run_progress_flow_uses_config_timeout(monkeypatch):
    captured = {}

    async def fake_poll(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(up, "get_user_id", lambda t: 1)
    monkeypatch.setattr(up, "poll_until_done", fake_poll)

    async def edit(_):
        return None

    asyncio.run(up.run_progress_flow(123, 7, edit))
    assert captured["timeout"] == BOT_PROGRESS_TIMEOUT
