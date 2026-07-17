"""Тесты API справочников: поиск препаратов (ГРЛС) и городов РФ."""
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


# ===== Города =====

def test_cities_search_min_2_chars(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/directory/cities?q=М", headers=_hdr()).status_code == 422


def test_cities_search_moscow(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/directory/cities?q=Моск", headers=_hdr())
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
    assert data[0]["name"] == "Москва"
    assert "lat" in data[0] and "lon" in data[0]


def test_cities_search_with_region(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/directory/cities?q=Курган", headers=_hdr())
    assert r.status_code == 200
    data = r.json()
    # Курган и Курганская область
    assert any(c["name"] == "Курган" for c in data)
    assert any("Курганская" in c["region"] for c in data)


def test_cities_search_no_results(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/directory/cities?q=Ъъъъ", headers=_hdr())
    assert r.status_code == 200
    assert r.json() == []


def test_cities_requires_auth(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/directory/cities?q=Москва").status_code == 401


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


# ===== Справочник городов (unit) =====

def test_cities_module_search():
    from botkin.reference.cities import search_cities
    results = search_cities("Моск")
    assert len(results) >= 1
    assert results[0]["name"] == "Москва"


def test_cities_module_coordinates():
    from botkin.reference.cities import get_city_coordinates
    coords = get_city_coordinates("Москва")
    assert coords is not None
    assert abs(coords[0] - 55.7558) < 0.01


def test_cities_module_short_query():
    from botkin.reference.cities import search_cities
    assert search_cities("М") == []


def test_cities_module_contains_fallback():
    from botkin.reference.cities import search_cities
    # "на-Дону" не начинается с "на-Д", но contains найдёт
    results = search_cities("на-Д")
    assert any("Ростов" in r["name"] for r in results)
