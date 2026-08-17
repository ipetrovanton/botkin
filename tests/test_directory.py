"""Тесты API справочника препаратов (ГРЛС)."""
import importlib

import botkin.config
import botkin.db.connection


def _client(monkeypatch, tmp_path):
    db = tmp_path / "dir.db"
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
    return {"X-Telegram-User-Id": "888"}


# ===== Препараты =====

def test_drugs_search_requires_data(monkeypatch, tmp_path):
    """На пустой БД (без индексации ГРЛС) поиск вернёт пустой список."""
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/directory/drugs?q=парацетамол", headers=_hdr())
    assert r.status_code == 200
    assert r.json() == []


def test_drugs_search_min_2_chars(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/directory/drugs?q=п", headers=_hdr()).status_code == 422


def test_drugs_requires_auth(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/directory/drugs?q=парацетамол").status_code == 401
