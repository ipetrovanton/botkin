"""Тесты API-роутов веб-кабинета (/api/*) через FastAPI TestClient."""
import importlib

import botkin.config
import botkin.db.connection
from botkin.db.repos import DocumentRepo, LabRepo, ReportRepo, UserRepo

TG = "777"


def _seed(conn):
    uid = UserRepo(conn).get_or_create(int(TG))
    repo = DocumentRepo(conn, uid)
    d1 = repo.create(source_path="/tmp/a1.pdf")
    repo.set_doc_type(d1, "analysis")
    repo.set_metadata(d1, title="ОАК", clinic="Инвитро")
    repo.set_status(d1, "extracted")
    d2 = repo.create(source_path="/tmp/r1.jpg")
    repo.set_doc_type(d2, "doctor_report")
    repo.set_metadata(d2, title="Заключение", clinic="Садко")
    repo.set_status(d2, "extracted")
    ReportRepo(conn, uid).save([{
        "document_id": d2, "user_id": uid, "diagnosis": "ОРВИ",
        "recommendations_json": '["Покой"]', "complaints_json": "[]",
        "anamnesis": None, "medications_json": "[]",
        "medications_normalized_json": None,
        "visit_date": "2026-06-10", "doctor_name": "Смирнов С.С.",
        "department": "Терапия",
    }])
    LabRepo(conn, uid).save_results([{
        "document_id": d1, "user_id": uid, "analyte_code": None,
        "analyte_name": "Гемоглобин", "value_num": 140.0, "value_text": None,
        "unit": "г/л", "ref_low": 120.0, "ref_high": 160.0,
        "ref_operator": None, "ref_text": None, "taken_at": "2026-06-05",
        "source_table_cell": None, "value_raw": "140", "unit_raw": None,
        "taken_at_raw": None, "analyte_canonical": "Гемоглобин", "loinc": None,
        "nmu_code": None, "analyte_group": None, "match_status": "matched",
        "unit_expected": "г/л", "unit_mismatch": 0,
    }])
    return uid, d1, d2


def _client(monkeypatch, tmp_path, *, seed=True):
    """TestClient с временной БД; warmup-задача в lifespan подавляется monkeypatch.

    Сидирование происходит ПОСЛЕ reload config/init_db — иначе данные уйдут в старую БД.
    Использует get_conn из уже перезагруженного модуля botkin.db.connection.
    """
    db = tmp_path / "cab.db"
    monkeypatch.setenv("SQLITE_PATH", str(db))
    importlib.reload(botkin.config)
    importlib.reload(botkin.db.connection)
    botkin.db.connection.init_db()

    # Заглушим warmup, чтобы lifespan не запускал фоновую задачу с Ollama.
    import botkin.llm.client as llm_client
    monkeypatch.setattr(llm_client, "warmup", lambda: None)

    from fastapi.testclient import TestClient
    import botkin.api.app as appmod
    importlib.reload(appmod)

    if seed:
        with botkin.db.connection.get_conn() as conn:
            uid, d1, d2 = _seed(conn)
        # Сохраним id для тестов, обращающихся к конкретному документу.
        _client.last_ids = {"uid": uid, "d1": d1, "d2": d2}
    return TestClient(appmod.app)


def test_api_me(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/me", headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 200
    assert r.json()["telegram_user_id"] == int(TG)


def test_api_documents_filter_by_type(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/documents", params={"doc_type": "analysis", "limit": 10},
                   headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["doc_type"] == "analysis"


def test_api_documents_detail_analysis(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get(f"/api/documents/{_client.last_ids['d1']}", headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "analysis"
    assert len(data["labs"]) == 1
    assert data["labs"][0]["analyte_name"] == "Гемоглобин"


def test_api_documents_detail_doctor_report(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get(f"/api/documents/{_client.last_ids['d2']}", headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "doctor_report"
    assert data["reports"][0]["doctor_name"] == "Смирнов С.С."
    assert data["reports"][0]["recommendations"] == ["Покой"]


def test_api_documents_detail_not_found(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/documents/9999", headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 404


def test_api_documents_status(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get(f"/api/documents/{_client.last_ids['d1']}/status", headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 200
    assert r.json()["status"] == "extracted"


def test_api_dynamics(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/dynamics", params={"name": "Гемоглобин"},
                   headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 200
    data = r.json()
    assert data["analyte"] == "Гемоглобин"
    assert len(data["points"]) == 1
    assert data["ref_low"] == 120.0 and data["ref_high"] == 160.0


def test_api_dynamics_not_found(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/dynamics", params={"name": "Несуществующий"},
                   headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 404


def test_api_selectors(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    h = {"X-Telegram-User-Id": TG}
    assert "Инвитро" in client.get("/api/clinics", headers=h).json()
    docs = client.get("/api/doctors", headers=h).json()
    assert any(d["doctor_name"] == "Смирнов С.С." for d in docs)
    assert "Гемоглобин" in client.get("/api/analytes", headers=h).json()


def test_api_stats(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/stats", headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 200
    s = r.json()
    assert s["total"] == 2
    assert s["labs"] == 1
    assert s["reports"] == 1


def test_api_reports(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/reports", params={"doctor": "Смирнов С.С."},
                   headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["clinic"] == "Садко"


def test_api_isolates_users(monkeypatch, tmp_path):
    """Чужой пользователь не видит документы 777."""
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/documents", headers={"X-Telegram-User-Id": "8888"})
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_static_index_served(monkeypatch, tmp_path):
    """SPA index.html отдаётся в корне (StaticFiles html=True)."""
    client = _client(monkeypatch, tmp_path, seed=False)
    r = client.get("/")
    assert r.status_code == 200
    assert "cabinet()" in r.text
    assert "alpine.min.js" in r.text
