import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock


def test_run_progress_flow_shows_card_on_success(monkeypatch):
    import botkin.bot.handlers.upload as up

    async def fake_poll(**kwargs):
        return "extracted"

    monkeypatch.setattr(up, "poll_until_done", fake_poll)
    monkeypatch.setattr(up, "get_user_id", lambda tg: 1)
    monkeypatch.setattr(up, "render_document_card", lambda doc_id, uid: "КАРТОЧКА #9")

    edit = AsyncMock()
    delivered = {"flag": False}
    monkeypatch.setattr(up, "claim_delivery_for",
                        lambda doc_id, uid: delivered.__setitem__("flag", True) or True)

    asyncio.run(up.run_progress_flow(tg_user_id=10, doc_id=9, edit=edit))
    edit.assert_awaited()                       # финал отрисован
    assert "КАРТОЧКА #9" in edit.await_args.args[0]
    assert delivered["flag"] is True            # доставка захвачена


def test_on_photo_warns_before_progress(monkeypatch):
    """Подсказка/предупреждение о разрешении приходит ДО прогресс-сообщения."""
    import botkin.bot.handlers.upload as up

    monkeypatch.setattr(up, "_upload_to_api", AsyncMock(return_value={"document_id": 13}))

    async def _noop_flow(*a, **k):
        return None

    monkeypatch.setattr(up, "run_progress_flow", _noop_flow)

    order = []

    async def fake_answer(text):
        order.append(text)
        return SimpleNamespace(edit_text=AsyncMock())

    file_bytes = SimpleNamespace(read=lambda: b"x")
    bot = SimpleNamespace(
        get_file=AsyncMock(return_value=SimpleNamespace(file_path="p")),
        download_file=AsyncMock(return_value=file_bytes),
    )
    photo = SimpleNamespace(file_id="f", file_unique_id="u", width=800)  # < 1500 → low-res
    message = SimpleNamespace(
        photo=[photo], bot=bot,
        from_user=SimpleNamespace(id=10), answer=fake_answer,
    )

    asyncio.run(up.on_photo(message))

    warn_idx = next(i for i, t in enumerate(order) if "разрешении" in t)
    progress_idx = next(i for i, t in enumerate(order) if "обрабатываю" in t)
    assert warn_idx < progress_idx, f"предупреждение должно идти раньше прогресса: {order}"


def test_run_progress_flow_timeout(monkeypatch):
    import botkin.bot.handlers.upload as up

    async def fake_poll(**kwargs):
        return None

    monkeypatch.setattr(up, "poll_until_done", fake_poll)
    monkeypatch.setattr(up, "get_user_id", lambda tg: 1)
    edit = AsyncMock()
    asyncio.run(up.run_progress_flow(tg_user_id=10, doc_id=9, edit=edit))
    assert "затянул" in edit.await_args.args[0].lower()
