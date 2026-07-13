"""P2: notify_user не должен пересоздавать Bot (и aiohttp-сессию) на каждый вызов."""
from botkin.pipeline import notifications


def test_shared_bot_reused_per_token():
    notifications._shared_bot.cache_clear()
    token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    b1 = notifications._shared_bot(token)
    b2 = notifications._shared_bot(token)
    assert b1 is b2
