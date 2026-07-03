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


# ===== Аутентификация: обязательный заголовок и дебаг-флаг =====

def test_api_requires_header_when_no_debug_flag(monkeypatch, tmp_path):
    """Без X-Telegram-User-Id и без флага — 401, а не молчаливый доступ."""
    monkeypatch.delenv("WEB_DEBUG_USER_ID", raising=False)
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/me")
    assert r.status_code == 401


def test_api_debug_flag_allows_headerless_access(monkeypatch, tmp_path):
    """WEB_DEBUG_USER_ID: локальный запуск/дебаг без настройки заголовка."""
    monkeypatch.setenv("WEB_DEBUG_USER_ID", TG)
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/me")
    assert r.status_code == 200
    assert r.json()["telegram_user_id"] == int(TG)


# ===== Удаление документов =====

def test_api_delete_document_removes_data_and_file(monkeypatch, tmp_path):
    """DELETE удаляет документ, его показатели и файл-исходник; статистика чистая."""
    src = tmp_path / "del_me.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    client = _client(monkeypatch, tmp_path)
    with botkin.db.connection.get_conn() as conn:
        uid = UserRepo(conn).get_id(int(TG))
        repo = DocumentRepo(conn, uid)
        did = repo.create(source_path=str(src))
        repo.set_doc_type(did, "analysis")
        repo.set_status(did, "extracted")
        LabRepo(conn, uid).save_results([{
            "document_id": did, "user_id": uid, "analyte_code": None,
            "analyte_name": "Глюкоза", "value_num": 5.0, "value_text": None,
            "unit": "ммоль/л", "ref_low": 3.9, "ref_high": 6.1,
            "ref_operator": None, "ref_text": None, "taken_at": "2026-06-05",
            "source_table_cell": None, "value_raw": "5.0", "unit_raw": None,
            "taken_at_raw": None, "analyte_canonical": "Глюкоза", "loinc": None,
            "nmu_code": None, "analyte_group": None, "match_status": "matched",
            "unit_expected": "ммоль/л", "unit_mismatch": 0,
        }])

    r = client.delete(f"/api/documents/{did}", headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 200
    assert r.json()["deleted"] == 1
    assert not src.exists()
    # Документ и его данные исчезли — из карточек и статистики.
    assert client.get(f"/api/documents/{did}",
                      headers={"X-Telegram-User-Id": TG}).status_code == 404
    stats = client.get("/api/stats", headers={"X-Telegram-User-Id": TG}).json()
    assert stats["labs"] == 1  # остался только гемоглобин из _seed


def test_api_delete_foreign_document_404(monkeypatch, tmp_path):
    """Чужой документ удалить нельзя — 404, данные целы."""
    client = _client(monkeypatch, tmp_path)
    d1 = _client.last_ids["d1"]
    r = client.delete(f"/api/documents/{d1}", headers={"X-Telegram-User-Id": "999"})
    assert r.status_code == 404
    assert client.get(f"/api/documents/{d1}",
                      headers={"X-Telegram-User-Id": TG}).status_code == 200


def test_api_delete_batch(monkeypatch, tmp_path):
    """Массовое удаление: свои удаляются, чужие/несуществующие тихо пропускаются."""
    client = _client(monkeypatch, tmp_path)
    d1, d2 = _client.last_ids["d1"], _client.last_ids["d2"]
    r = client.post("/api/documents/delete-batch", json={"ids": [d1, d2, 424242]},
                    headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 200
    assert r.json()["deleted"] == 2
    stats = client.get("/api/stats", headers={"X-Telegram-User-Id": TG}).json()
    assert stats["total"] == 0 and stats["labs"] == 0 and stats["reports"] == 0


# ===== Исходник документа =====

def test_api_document_source_serves_file(monkeypatch, tmp_path):
    src = tmp_path / "orig.pdf"
    src.write_bytes(b"%PDF-1.4 original")
    client = _client(monkeypatch, tmp_path)
    with botkin.db.connection.get_conn() as conn:
        uid = UserRepo(conn).get_id(int(TG))
        did = DocumentRepo(conn, uid).create(source_path=str(src))
    r = client.get(f"/api/documents/{did}/source", headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content == b"%PDF-1.4 original"


def test_api_document_source_missing_file_404(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get(f"/api/documents/{_client.last_ids['d1']}/source",
                   headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 404  # у _seed путь /tmp/a1.pdf не существует


# ===== Репарсинг =====

def test_api_reparse_clears_data_and_requeues(monkeypatch, tmp_path):
    """Обновление документа: данные очищаются, статус received, pipeline перезапущен."""
    src = tmp_path / "re.pdf"
    src.write_bytes(b"%PDF-1.4 re")
    client = _client(monkeypatch, tmp_path)
    with botkin.db.connection.get_conn() as conn:
        uid = UserRepo(conn).get_id(int(TG))
        repo = DocumentRepo(conn, uid)
        did = repo.create(source_path=str(src))
        repo.set_doc_type(did, "analysis")
        repo.set_status(did, "extracted")
        LabRepo(conn, uid).save_results([{
            "document_id": did, "user_id": uid, "analyte_code": None,
            "analyte_name": "Глюкоза", "value_num": 5.0, "value_text": None,
            "unit": "ммоль/л", "ref_low": 3.9, "ref_high": 6.1,
            "ref_operator": None, "ref_text": None, "taken_at": "2026-06-05",
            "source_table_cell": None, "value_raw": "5.0", "unit_raw": None,
            "taken_at_raw": None, "analyte_canonical": "Глюкоза", "loinc": None,
            "nmu_code": None, "analyte_group": None, "match_status": "matched",
            "unit_expected": "ммоль/л", "unit_mismatch": 0,
        }])

    calls = []
    import botkin.api.routes.documents as docs_route

    async def fake_process(doc_id, tg_id):
        calls.append((doc_id, tg_id))

    monkeypatch.setattr(docs_route, "process_document", fake_process)
    r = client.post(f"/api/documents/{did}/reparse", headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 200
    assert r.json()["status"] == "received"
    assert calls == [(did, int(TG))]
    # Данные очищены, статус сброшен.
    detail = client.get(f"/api/documents/{did}", headers={"X-Telegram-User-Id": TG}).json()
    assert detail["labs"] == []
    assert detail["document"]["status"] == "received"


def test_api_reparse_missing_source_conflict(monkeypatch, tmp_path):
    """Файл-исходник утрачен — репарсить нечего, 409 с объяснением."""
    client = _client(monkeypatch, tmp_path)
    r = client.post(f"/api/documents/{_client.last_ids['d1']}/reparse",
                    headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 409


def test_api_upload_stores_file_sha256(monkeypatch, tmp_path):
    """Upload сохраняет sha256 содержимого — точный признак повторной загрузки."""
    import hashlib

    client = _client(monkeypatch, tmp_path)
    import botkin.api.routes.upload as upload_route

    async def fake_process(doc_id, tg_id):
        pass

    monkeypatch.setattr(upload_route, "process_document", fake_process)
    body = b"%PDF-1.4 sha test"
    r = client.post("/upload", files={"file": ("t.pdf", body, "application/pdf")},
                    headers={"X-Telegram-User-Id": TG})
    assert r.status_code == 200
    doc_id = r.json()["document_id"]
    with botkin.db.connection.get_conn() as conn:
        row = conn.execute(
            "SELECT file_sha256 FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
    assert row["file_sha256"] == hashlib.sha256(body).hexdigest()
