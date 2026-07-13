"""Тесты API администратора (/api/admin/*): роли, CRUD пользователей и анализов."""
import importlib

import botkin.config
import botkin.db.connection
from botkin.db.repos import DocumentRepo, LabRepo, UserRepo

ADMIN_TG = "100"
USER_TG = "200"


def _client(monkeypatch, tmp_path):
    """TestClient с временной БД; ADMIN_TG бутстрапится админом через env."""
    db = tmp_path / "admin.db"
    monkeypatch.setenv("SQLITE_PATH", str(db))
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", ADMIN_TG)
    importlib.reload(botkin.config)
    importlib.reload(botkin.db.connection)
    botkin.db.connection.init_db()

    import botkin.llm.client as llm_client
    monkeypatch.setattr(llm_client, "warmup", lambda: None)

    from fastapi.testclient import TestClient
    import botkin.api.deps as depsmod
    import botkin.api.routes.admin as adminmod
    import botkin.api.app as appmod
    importlib.reload(depsmod)
    importlib.reload(adminmod)
    importlib.reload(appmod)
    return TestClient(appmod.app)


def _hdr(tg: str) -> dict:
    return {"X-Telegram-User-Id": tg}


def _bootstrap(client) -> tuple[int, int]:
    """Регистрирует админа и обычного пользователя через /api/me; возвращает их id."""
    admin_id = client.get("/api/me", headers=_hdr(ADMIN_TG)).json()["id"]
    user_id = client.get("/api/me", headers=_hdr(USER_TG)).json()["id"]
    return admin_id, user_id


def test_me_returns_role(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/me", headers=_hdr(ADMIN_TG)).json()["role"] == "admin"
    assert client.get("/api/me", headers=_hdr(USER_TG)).json()["role"] == "user"


def test_admin_routes_forbidden_for_user(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _bootstrap(client)
    r = client.get("/api/admin/users", headers=_hdr(USER_TG))
    assert r.status_code == 403


def test_admin_lists_users_with_counters(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _, user_id = _bootstrap(client)
    with botkin.db.connection.get_conn() as conn:
        d = DocumentRepo(conn, user_id).create(source_path="/tmp/x.pdf")
        LabRepo(conn, user_id).insert_manual(d, {"analyte_name": "Глюкоза", "value_num": 5.1})
    items = client.get("/api/admin/users", headers=_hdr(ADMIN_TG)).json()["items"]
    target = next(u for u in items if u["id"] == user_id)
    assert target["documents"] == 1
    assert target["lab_results"] == 1


def test_admin_creates_and_deletes_user(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _bootstrap(client)
    r = client.post("/api/admin/users", json={"telegram_user_id": 300, "display_name": "Тест"},
                    headers=_hdr(ADMIN_TG))
    assert r.status_code == 201
    new_id = r.json()["id"]
    # дубликат — 409
    r2 = client.post("/api/admin/users", json={"telegram_user_id": 300}, headers=_hdr(ADMIN_TG))
    assert r2.status_code == 409
    # удаление
    r3 = client.delete(f"/api/admin/users/{new_id}", headers=_hdr(ADMIN_TG))
    assert r3.status_code == 200
    with botkin.db.connection.get_conn() as conn:
        assert UserRepo(conn).get(new_id) is None


def test_admin_cannot_delete_self_or_demote_last_admin(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    admin_id, _ = _bootstrap(client)
    assert client.delete(f"/api/admin/users/{admin_id}",
                         headers=_hdr(ADMIN_TG)).status_code == 409
    r = client.patch(f"/api/admin/users/{admin_id}", json={"role": "user"},
                     headers=_hdr(ADMIN_TG))
    assert r.status_code == 409


def test_admin_promotes_user(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _, user_id = _bootstrap(client)
    r = client.patch(f"/api/admin/users/{user_id}", json={"role": "admin"},
                     headers=_hdr(ADMIN_TG))
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_admin_lab_crud(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _, user_id = _bootstrap(client)
    # создание без документа → служебный «ручной» документ
    r = client.post(f"/api/admin/users/{user_id}/labs",
                    json={"analyte_name": "Ферритин", "value_num": 35.0, "unit": "нг/мл"},
                    headers=_hdr(ADMIN_TG))
    assert r.status_code == 201
    lab = r.json()
    assert lab["match_status"] == "manual"
    # правка
    r2 = client.patch(f"/api/admin/labs/{lab['id']}", params={"user_id": user_id},
                      json={"value_num": 40.0}, headers=_hdr(ADMIN_TG))
    assert r2.status_code == 200
    assert r2.json()["value_num"] == 40.0
    assert r2.json()["analyte_name"] == "Ферритин"  # PATCH не затёр остальные поля
    # список
    items = client.get(f"/api/admin/users/{user_id}/labs",
                       headers=_hdr(ADMIN_TG)).json()["items"]
    assert len(items) == 1
    # удаление
    r3 = client.delete(f"/api/admin/labs/{lab['id']}", params={"user_id": user_id},
                       headers=_hdr(ADMIN_TG))
    assert r3.status_code == 200
    assert client.get(f"/api/admin/users/{user_id}/labs",
                      headers=_hdr(ADMIN_TG)).json()["total"] == 0


def test_user_data_isolated_between_users(monkeypatch, tmp_path):
    """Разделение по пользователям: чужие документы не видны в ленте."""
    client = _client(monkeypatch, tmp_path)
    _, user_id = _bootstrap(client)
    with botkin.db.connection.get_conn() as conn:
        DocumentRepo(conn, user_id).create(source_path="/tmp/y.pdf")
    other = client.get("/api/documents", headers=_hdr("999")).json()
    assert other["total"] == 0
    own = client.get("/api/documents", headers=_hdr(USER_TG)).json()
    assert own["total"] == 1
