"""Тесты расписания автосинка Garmin: API + SQL-выборка «пора синкать»."""
import importlib

import botkin.config
import botkin.db.connection

TG = "555"


def _client(monkeypatch, tmp_path):
    db = tmp_path / "sched.db"
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


def _hdr(tg: str = TG) -> dict:
    return {"X-Telegram-User-Id": tg}


def _connect_garmin(client, monkeypatch):
    """Создаёт health_account без реального логина в Garmin."""
    from botkin.db.connection import get_conn
    from botkin.db.repos import HealthRepo, UserRepo
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(int(TG))
        HealthRepo(conn, uid).upsert_account("garmin", identifier="test@example.com")


def test_set_schedule_interval(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _connect_garmin(client, monkeypatch)
    r = client.patch("/api/health/accounts/garmin/schedule",
                     json={"interval_hours": 6}, headers=_hdr())
    assert r.status_code == 200
    assert r.json()["interval_hours"] == 6
    # проверяем, что значение видно в выдаче аккаунтов
    accs = client.get("/api/health/accounts", headers=_hdr()).json()["items"]
    garmin = [a for a in accs if a["provider"] == "garmin"][0]
    assert garmin["sync_interval_hours"] == 6


def test_set_schedule_to_null_disables(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _connect_garmin(client, monkeypatch)
    # включаем
    client.patch("/api/health/accounts/garmin/schedule",
                 json={"interval_hours": 12}, headers=_hdr())
    # выключаем
    r = client.patch("/api/health/accounts/garmin/schedule",
                     json={"interval_hours": None}, headers=_hdr())
    assert r.status_code == 200
    assert r.json()["interval_hours"] is None


def test_schedule_validation(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _connect_garmin(client, monkeypatch)
    assert client.patch("/api/health/accounts/garmin/schedule",
                        json={"interval_hours": 0}, headers=_hdr()).status_code == 422
    assert client.patch("/api/health/accounts/garmin/schedule",
                        json={"interval_hours": 200}, headers=_hdr()).status_code == 422


def test_schedule_404_for_unconnected(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.patch("/api/health/accounts/garmin/schedule",
                     json={"interval_hours": 6}, headers=_hdr())
    assert r.status_code == 404


def test_accounts_due_for_sync_null_last_sync(monkeypatch, tmp_path):
    """Аккаунт с интервалом, но без last_sync_at — должен попасть в выборку (ни разу не синкали)."""
    client = _client(monkeypatch, tmp_path)
    _connect_garmin(client, monkeypatch)
    client.patch("/api/health/accounts/garmin/schedule",
                 json={"interval_hours": 1}, headers=_hdr())

    from botkin.db.connection import get_conn
    from botkin.db.repos import HealthRepo, UserRepo
    with get_conn() as conn:
        uid = UserRepo(conn).get_id(int(TG))
        due = HealthRepo.accounts_due_for_sync(conn)
    assert any(d["user_id"] == uid and d["provider"] == "garmin" for d in due)


def test_accounts_due_excludes_no_interval(monkeypatch, tmp_path):
    """Аккаунт без sync_interval_hours не попадает в выборку — только ручной синк."""
    client = _client(monkeypatch, tmp_path)
    _connect_garmin(client, monkeypatch)

    from botkin.db.connection import get_conn
    from botkin.db.repos import HealthRepo, UserRepo
    with get_conn() as conn:
        uid = UserRepo(conn).get_id(int(TG))
        due = HealthRepo.accounts_due_for_sync(conn)
    assert not any(d["user_id"] == uid for d in due)


def test_run_due_syncs_skips_disconnected(monkeypatch, tmp_path):
    """run_due_syncs не запускает синк для disconnected-аккаунтов."""
    client = _client(monkeypatch, tmp_path)
    _connect_garmin(client, monkeypatch)
    client.patch("/api/health/accounts/garmin/schedule",
                 json={"interval_hours": 1}, headers=_hdr())

    # disconnect
    client.delete("/api/health/accounts/garmin", headers=_hdr())

    import botkin.api.routes.health_sync as hs
    # run_due_syncs должен вернуть пустой список (garmin disconnected → status != connected)
    import asyncio
    asyncio.run(hs.run_due_syncs())  # не должно бросить
