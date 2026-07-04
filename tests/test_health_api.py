"""Тесты API здоровья (/api/health/*) через FastAPI TestClient. Без внешних сервисов:
Garmin/Ollama не дёргаются — коннектор подменяется, ingest-пути детерминированы."""
import importlib
import io
import zipfile

import botkin.config
import botkin.db.connection

TG = "888"


def _client(monkeypatch, tmp_path):
    db = tmp_path / "health.db"
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


def _h():
    return {"X-Telegram-User-Id": TG}


def test_accounts_empty(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/health/accounts", headers=_h())
    assert r.status_code == 200
    data = r.json()
    assert data["items"] == []
    assert "strava_configured" in data


def test_connect_garmin_persists_account(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    import botkin.api.routes.health_sync as hs
    monkeypatch.setattr(hs.garmin, "connect", lambda uid, email, pwd: {
        "identifier": email, "token_path": str(tmp_path / "tok"), "full_name": "Тест",
    })
    r = client.post("/api/health/connect/garmin", headers=_h(),
                    json={"email": "u@e.x", "password": "секрет"})
    assert r.status_code == 200
    assert r.json()["full_name"] == "Тест"
    accounts = client.get("/api/health/accounts", headers=_h()).json()["items"]
    assert accounts[0]["provider"] == "garmin"
    assert accounts[0]["identifier"] == "u@e.x"


def test_connect_garmin_failure_returns_502(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    import botkin.api.routes.health_sync as hs

    def boom(uid, email, pwd):
        raise RuntimeError("429")
    monkeypatch.setattr(hs.garmin, "connect", boom)
    r = client.post("/api/health/connect/garmin", headers=_h(),
                    json={"email": "u@e.x", "password": "x"})
    assert r.status_code == 502


def test_sync_without_account_conflict(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/health/sync/garmin", headers=_h())
    assert r.status_code == 409


def test_series_404_without_data(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/health/series", params={"metric": "steps"}, headers=_h())
    assert r.status_code == 404


def test_apple_ingest_saves_metrics(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    payload = {"data": {"metrics": [
        {"name": "resting_heart_rate", "units": "bpm",
         "data": [{"date": "2026-07-01 00:00:00 +0300", "qty": 58}]},
    ]}}
    r = client.post("/api/health/apple/ingest", headers=_h(), json=payload)
    assert r.status_code == 200
    assert r.json()["metrics"] == 1
    series = client.get("/api/health/series",
                        params={"metric": "resting_heart_rate"}, headers=_h()).json()
    assert series["points"][0]["value_num"] == 58.0
    accounts = client.get("/api/health/accounts", headers=_h()).json()["items"]
    assert accounts[0]["provider"] == "apple_health"


def test_apple_import_export_zip(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    xml = """<?xml version="1.0"?><HealthData>
      <Record type="HKQuantityTypeIdentifierHeartRate" unit="count/min"
              startDate="2026-07-01 10:00:00 +0300" value="70"/>
    </HealthData>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("export.xml", xml)
    r = client.post("/api/health/apple/import", headers=_h(),
                    files={"file": ("export.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200
    assert r.json()["metrics"] == 1


def test_apple_import_garbage_422(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/health/apple/import", headers=_h(),
                    files={"file": ("x.zip", b"not a zip", "application/zip")})
    assert r.status_code == 422


def test_strava_authorize_unconfigured_501(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    import botkin.api.routes.health_sync as hs
    monkeypatch.setattr(hs.strava, "is_configured", lambda: False)
    r = client.get("/api/health/strava/authorize",
                   params={"redirect_uri": "http://localhost:8000"}, headers=_h())
    assert r.status_code == 501


def test_rag_status_empty_index(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/rag/status")
    assert r.status_code == 200
    assert r.json()["vectors"] == 0
