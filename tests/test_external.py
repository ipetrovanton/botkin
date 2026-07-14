"""Тесты внешних данных: астрология (чистая логика), API /api/external/today."""
import importlib

import botkin.config
import botkin.db.connection


def _client(monkeypatch, tmp_path):
    db = tmp_path / "ext.db"
    monkeypatch.setenv("SQLITE_PATH", str(db))
    importlib.reload(botkin.config)
    importlib.reload(botkin.db.connection)
    botkin.db.connection.init_db()

    import botkin.llm.client as llm_client
    monkeypatch.setattr(llm_client, "warmup", lambda: None)

    from fastapi.testclient import TestClient
    import botkin.api.app as appmod
    importlib.reload(appmod)
    return TestClient(appmod.app)


def _hdr() -> dict:
    return {"X-Telegram-User-Id": "777"}


# ===== Астрология (чистая логика, без сети) =====

def test_zodiac_capricorn():
    from botkin.external.astrology import get_zodiac_sign
    assert get_zodiac_sign("1990-01-05") == "Козерог"


def test_zodiac_leo():
    from botkin.external.astrology import get_zodiac_sign
    assert get_zodiac_sign("1990-08-10") == "Лев"


def test_zodiac_boundary():
    from botkin.external.astrology import get_zodiac_sign
    # Граница Овен/Телец: 20 апреля — Телец
    assert get_zodiac_sign("1990-04-20") == "Телец"
    assert get_zodiac_sign("1990-04-19") == "Овен"


def test_zodiac_none_for_invalid():
    from botkin.external.astrology import get_zodiac_sign
    assert get_zodiac_sign(None) is None
    assert get_zodiac_sign("not-a-date") is None


def test_horoscope_deterministic_per_day():
    from botkin.external.astrology import get_daily_horoscope
    h1 = get_daily_horoscope("1990-08-10")
    h2 = get_daily_horoscope("1990-08-10")
    assert h1 is not None
    assert h1 == h2  # один и тот же текст в течение дня


def test_horoscope_none_without_birth_date():
    from botkin.external.astrology import get_daily_horoscope
    assert get_daily_horoscope(None) is None


# ===== API /api/external/today =====

def test_external_today_requires_auth(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/external/today").status_code == 401


def test_external_today_returns_structure(monkeypatch, tmp_path):
    """API возвращает структуру с полями weather/geomagnetic/horoscope.

    Внешние API могут быть недоступны в CI — проверяем только структуру, не значения.
    """
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/external/today", headers=_hdr())
    assert r.status_code == 200
    data = r.json()
    assert "weather" in data
    assert "geomagnetic" in data
    assert "horoscope" in data


def test_external_today_with_profile_coordinates(monkeypatch, tmp_path):
    """Если в профиле есть координаты — они используются для погоды."""
    client = _client(monkeypatch, tmp_path)
    # Сохраняем профиль с координатами
    client.put("/api/patient/profile", json={"latitude": 59.93, "longitude": 30.34},
               headers=_hdr())
    r = client.get("/api/external/today", headers=_hdr())
    assert r.status_code == 200
