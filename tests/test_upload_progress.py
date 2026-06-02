import asyncio
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


def test_run_progress_flow_timeout(monkeypatch):
    import botkin.bot.handlers.upload as up

    async def fake_poll(**kwargs):
        return None

    monkeypatch.setattr(up, "poll_until_done", fake_poll)
    monkeypatch.setattr(up, "get_user_id", lambda tg: 1)
    edit = AsyncMock()
    asyncio.run(up.run_progress_flow(tg_user_id=10, doc_id=9, edit=edit))
    assert "затянул" in edit.await_args.args[0].lower()
