"""Тесты форм пациента (/api/patient/*) и их учёта в RAG-контексте."""
import importlib

import botkin.config
import botkin.db.connection

TG = "333"


def _client(monkeypatch, tmp_path):
    db = tmp_path / "patient.db"
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


def test_profile_upsert_partial(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.put("/api/patient/profile",
                   json={"sex": "female", "height_cm": 168.0}, headers=_hdr())
    assert r.status_code == 200
    # частичное обновление не затирает ранее сохранённое
    r2 = client.put("/api/patient/profile", json={"weight_kg": 60.5}, headers=_hdr())
    body = r2.json()
    assert body["sex"] == "female"
    assert body["height_cm"] == 168.0
    assert body["weight_kg"] == 60.5


def test_profile_validation(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.put("/api/patient/profile", json={"sex": "attack"},
                      headers=_hdr()).status_code == 422
    assert client.put("/api/patient/profile", json={"height_cm": 5000},
                      headers=_hdr()).status_code == 422


def test_complaints_crud(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/patient/complaints",
                    json={"text": "Болит голова по утрам"}, headers=_hdr())
    assert r.status_code == 201
    cid = r.json()["id"]
    items = client.get("/api/patient/complaints", headers=_hdr()).json()["items"]
    assert len(items) == 1 and items[0]["text"] == "Болит голова по утрам"
    assert client.delete(f"/api/patient/complaints/{cid}", headers=_hdr()).status_code == 200
    assert client.get("/api/patient/complaints", headers=_hdr()).json()["items"] == []


def test_medications_lifecycle(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/patient/medications",
                    json={"name": "Магний B6", "dosage": "2 таб", "schedule": "утром"},
                    headers=_hdr())
    assert r.status_code == 201
    mid = r.json()["id"]
    # завершение приёма — история остаётся
    r2 = client.patch(f"/api/patient/medications/{mid}", params={"is_active": "false"},
                      headers=_hdr())
    assert r2.status_code == 200
    items = client.get("/api/patient/medications", headers=_hdr()).json()["items"]
    assert len(items) == 1 and items[0]["is_active"] == 0


def test_forms_isolated_by_user(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    client.post("/api/patient/complaints", json={"text": "Жалоба первого"}, headers=_hdr())
    other = client.get("/api/patient/complaints", headers=_hdr("777")).json()["items"]
    assert other == []


def test_rag_context_includes_forms(monkeypatch, tmp_path):
    """Формы пациента попадают в контекст рекомендаций (главное требование этапа)."""
    client = _client(monkeypatch, tmp_path)
    client.put("/api/patient/profile",
               json={"sex": "male", "birth_date": "1990-05-01", "weight_kg": 82.0,
                     "allergies": "пенициллин"},
               headers=_hdr())
    client.post("/api/patient/medications", json={"name": "Аторвастатин", "dosage": "10 мг"},
                headers=_hdr())
    client.post("/api/patient/complaints", json={"text": "Одышка при подъёме"}, headers=_hdr())

    from botkin.db.repos import UserRepo
    import botkin.rag.recommend as recommend
    importlib.reload(recommend)
    with botkin.db.connection.get_conn() as conn:
        uid = UserRepo(conn).get_id(int(TG))
    ctx = recommend.build_patient_context(uid)
    assert "мужской" in ctx
    assert "пенициллин" in ctx
    assert "Аторвастатин" in ctx
    assert "Одышка при подъёме" in ctx
    assert "Возраст:" in ctx  # вычислен из birth_date
