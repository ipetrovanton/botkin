"""Тесты аутентификации: регистрация, вход, сессия, доступ, роли."""
import importlib

import botkin.config
import botkin.db.connection
from botkin.db.repos import AuthRepo, UserRepo


def _client(monkeypatch, tmp_path):
    """TestClient с временной БД; warmup подавлен."""
    db = tmp_path / "auth.db"
    monkeypatch.setenv("SQLITE_PATH", str(db))
    monkeypatch.delenv("WEB_DEBUG_USER_ID", raising=False)
    importlib.reload(botkin.config)
    importlib.reload(botkin.db.connection)
    botkin.db.connection.init_db()

    import botkin.llm.client as llm_client
    monkeypatch.setattr(llm_client, "warmup", lambda: None)

    from fastapi.testclient import TestClient
    import botkin.api.app as appmod
    importlib.reload(appmod)
    return TestClient(appmod.app)


def test_register_creates_session_and_cookie(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/auth/register", json={
        "email": "test@example.com",
        "password": "secret123",
        "display_name": "Test User",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["email"] == "test@example.com"
    assert "user_id" in data
    # Cookie должна быть установлена
    cookies = r.headers.get("set-cookie", "")
    assert "botkin_session" in cookies


def test_register_duplicate_email_rejected(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    client.post("/api/auth/register", json={
        "email": "dup@example.com", "password": "secret123",
    })
    r = client.post("/api/auth/register", json={
        "email": "dup@example.com", "password": "other456",
    })
    assert r.status_code == 409


def test_login_with_correct_password(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    client.post("/api/auth/register", json={
        "email": "login@example.com", "password": "secret123",
    })
    # register уже установил cookie — очистим для чистоты теста
    client.cookies.clear()
    r = client.post("/api/auth/login", json={
        "email": "login@example.com", "password": "secret123",
    })
    assert r.status_code == 200
    assert r.json()["email"] == "login@example.com"


def test_login_with_wrong_password_rejected(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    client.post("/api/auth/register", json={
        "email": "wrong@example.com", "password": "secret123",
    })
    client.cookies.clear()
    r = client.post("/api/auth/login", json={
        "email": "wrong@example.com", "password": "wrongpass",
    })
    assert r.status_code == 401


def test_me_with_valid_session(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    client.post("/api/auth/register", json={
        "email": "me@example.com", "password": "secret123",
    })
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == "me@example.com"


def test_me_without_session_returns_401(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_logout_invalidates_session(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    client.post("/api/auth/register", json={
        "email": "out@example.com", "password": "secret123",
    })
    # Сессия активна
    assert client.get("/api/auth/me").status_code == 200
    # Выходим
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    # Cookie удалена, сессия невалидна
    assert client.get("/api/auth/me").status_code == 401


def test_api_endpoints_require_auth(monkeypatch, tmp_path):
    """Без cookie и без заголовка — 401."""
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/me")
    assert r.status_code == 401
    r = client.get("/api/documents")
    assert r.status_code == 401


def test_api_endpoints_work_with_session_cookie(monkeypatch, tmp_path):
    """Веб-пользователь с cookie имеет доступ к /api/*."""
    client = _client(monkeypatch, tmp_path)
    client.post("/api/auth/register", json={
        "email": "api@example.com", "password": "secret123",
    })
    r = client.get("/api/me")
    assert r.status_code == 200
    r = client.get("/api/documents")
    assert r.status_code == 200


def test_api_endpoints_still_work_with_telegram_header(monkeypatch, tmp_path):
    """Telegram-бот продолжает работать через X-Telegram-User-Id."""
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/me", headers={"X-Telegram-User-Id": "999"})
    assert r.status_code == 200
    assert r.json()["telegram_user_id"] == 999


def test_password_too_short_rejected(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/auth/register", json={
        "email": "short@example.com", "password": "12345",
    })
    assert r.status_code == 422


def test_invalid_email_rejected(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/auth/register", json={
        "email": "not-an-email", "password": "secret123",
    })
    assert r.status_code == 422


def test_session_persists_across_requests(monkeypatch, tmp_path):
    """Сессия сохраняется между запросами (cookie в TestClient)."""
    client = _client(monkeypatch, tmp_path)
    client.post("/api/auth/register", json={
        "email": "persist@example.com", "password": "secret123",
    })
    # Несколько запросов подряд — все должны быть авторизованы
    for _ in range(3):
        assert client.get("/api/auth/me").status_code == 200


def test_email_is_case_insensitive(monkeypatch, tmp_path):
    """Email приводится к нижнему регистру при регистрации и входе."""
    client = _client(monkeypatch, tmp_path)
    client.post("/api/auth/register", json={
        "email": "Case@Example.COM", "password": "secret123",
    })
    client.cookies.clear()
    r = client.post("/api/auth/login", json={
        "email": "case@example.com", "password": "secret123",
    })
    assert r.status_code == 200
