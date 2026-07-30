"""Тесты storage-слоя (LocalStorage) и API версий/замены исходника.

MinIO-бэкенд юнит-тестами не покрывается: требует живой сервер; его контракт
идентичен LocalStorage (save/open_local/delete/replace/versions) и проверяется
вручную через docker-compose.minio.yml.
"""
import importlib

import botkin.config
import botkin.db.connection

TG = "444"


def _reload_storage(monkeypatch, tmp_path):
    """storage.py с временной директорией исходников."""
    monkeypatch.setenv("SOURCES_DIR", str(tmp_path / "sources"))
    importlib.reload(botkin.config)
    import botkin.storage as storage
    importlib.reload(storage)
    return storage


def test_local_save_open_delete(monkeypatch, tmp_path):
    storage = _reload_storage(monkeypatch, tmp_path)
    uri = storage.default_storage().save(1, "test.pdf", b"data-1")
    path = storage.open_local(uri)
    assert path is not None and path.read_bytes() == b"data-1"
    storage.delete_quietly(uri)
    assert storage.open_local(uri) is None


def test_local_replace_keeps_versions(monkeypatch, tmp_path):
    storage = _reload_storage(monkeypatch, tmp_path)
    st = storage.default_storage()
    uri = st.save(1, "test.pdf", b"version-1")
    st.replace(uri, b"version-2-longer")
    assert storage.open_local(uri).read_bytes() == b"version-2-longer"
    versions = st.versions(uri)
    assert len(versions) == 2
    assert versions[0]["is_current"] is True
    assert versions[1]["is_current"] is False


def test_local_delete_removes_versions_too(monkeypatch, tmp_path):
    storage = _reload_storage(monkeypatch, tmp_path)
    st = storage.default_storage()
    uri = st.save(1, "test.pdf", b"v1")
    st.replace(uri, b"v2")
    st.delete(uri)
    assert st.versions(uri) == []


def test_manual_uri_ignored(monkeypatch, tmp_path):
    storage = _reload_storage(monkeypatch, tmp_path)
    assert storage.open_local("manual://admin") is None
    storage.delete_quietly("manual://admin")  # не бросает
    assert storage.is_stored_file("manual://admin") is False
    assert storage.is_stored_file(None) is False


def _client(monkeypatch, tmp_path):
    db = tmp_path / "st.db"
    monkeypatch.setenv("SQLITE_PATH", str(db))
    monkeypatch.setenv("SOURCES_DIR", str(tmp_path / "sources"))
    importlib.reload(botkin.config)
    importlib.reload(botkin.db.connection)
    botkin.db.connection.init_db()
    import botkin.storage as storage
    importlib.reload(storage)

    import botkin.llm.client as llm_client
    monkeypatch.setattr(llm_client, "warmup", lambda: None)
    # Пайплайн не должен реально запускаться после replace (Ollama нет в тестах).
    import botkin.pipeline.orchestrator as orch
    monkeypatch.setattr(orch, "process_document", lambda *a, **k: None)

    from fastapi.testclient import TestClient
    import botkin.api.routes.upload as upmod
    import botkin.api.routes.documents as docmod
    import botkin.api.app as appmod
    importlib.reload(upmod)
    importlib.reload(docmod)
    importlib.reload(appmod)
    monkeypatch.setattr(docmod, "process_document", lambda *a, **k: None)
    import botkin.api.services.documents as docs_svc
    monkeypatch.setattr(docs_svc, "process_document", lambda *a, **k: None)
    return TestClient(appmod.app)


_PDF_STUB = b"%PDF-1.4 test content"


def test_api_upload_replace_versions_flow(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    hdr = {"X-Telegram-User-Id": TG}
    # загрузка
    r = client.post("/upload", files={"file": ("a.pdf", _PDF_STUB, "application/pdf")},
                    headers=hdr)
    assert r.status_code == 200
    doc_id = r.json()["document_id"]
    # одна версия
    v = client.get(f"/api/documents/{doc_id}/versions", headers=hdr).json()["items"]
    assert len(v) == 1
    # замена тем же файлом — 409 (no-op)
    r2 = client.post(f"/api/documents/{doc_id}/replace",
                     files={"file": ("a.pdf", _PDF_STUB, "application/pdf")}, headers=hdr)
    assert r2.status_code == 409
    # замена новым содержимым
    r3 = client.post(f"/api/documents/{doc_id}/replace",
                     files={"file": ("a2.pdf", b"%PDF-1.4 updated", "application/pdf")},
                     headers=hdr)
    assert r3.status_code == 200
    assert r3.json()["status"] == "received"  # ушёл на перераспознавание
    v2 = client.get(f"/api/documents/{doc_id}/versions", headers=hdr).json()["items"]
    assert len(v2) == 2
    # оригинал отдаёт новое содержимое
    src = client.get(f"/api/documents/{doc_id}/source", headers=hdr)
    assert src.content == b"%PDF-1.4 updated"


def test_api_replace_validates_content(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    hdr = {"X-Telegram-User-Id": TG}
    r = client.post("/upload", files={"file": ("a.pdf", _PDF_STUB, "application/pdf")},
                    headers=hdr)
    doc_id = r.json()["document_id"]
    # мусорное содержимое — 415
    r2 = client.post(f"/api/documents/{doc_id}/replace",
                     files={"file": ("x.bin", b"\x00\x01\x02garbage", "application/octet-stream")},
                     headers=hdr)
    assert r2.status_code == 415
