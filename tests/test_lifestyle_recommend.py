"""Lifestyle-рекомендации: агрегация всех источников + вызов uncensored-модели (мок)."""
import importlib
import json
from types import SimpleNamespace

import botkin.config
import botkin.db.connection

TG = "444"


def _client(monkeypatch, tmp_path):
    db = tmp_path / "lifestyle.db"
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
    return {"X-Telegram-User-Id": TG}


def _seed_reports(uid: int) -> None:
    with botkin.db.connection.get_conn() as conn:
        conn.execute(
            "INSERT INTO documents (user_id, source_path, doc_type, status)"
            " VALUES (?, 'x.pdf', 'doctor_report', 'extracted')", (uid,))
        doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO doctor_reports (document_id, user_id, diagnosis,"
            " recommendations_json, medications_json, visit_date, doctor_name)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (doc_id, uid, "Гипертоническая болезнь I ст.",
             json.dumps(["Ограничить соль", "Контроль АД"]),
             json.dumps(["Лозартан 50 мг"]), "2026-08-01", "Иванов И.И."))
        conn.commit()


def _fake_response(text: str):
    msg = SimpleNamespace(content=text, reasoning_content=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)], usage=None,
    )


def test_lifestyle_context_includes_doctor_reports(monkeypatch, tmp_path):
    """Диагнозы и рекомендации врача попадают в агрегированную картину пациента."""
    _client(monkeypatch, tmp_path)
    from botkin.db.repos import UserRepo
    with botkin.db.connection.get_conn() as conn:
        UserRepo(conn).get_or_create(int(TG))
        uid = UserRepo(conn).get_id(int(TG))
    _seed_reports(uid)

    import botkin.rag.recommend as recommend
    importlib.reload(recommend)
    ctx = recommend._patient_context(uid)
    assert "Гипертоническая болезнь" in ctx
    assert "Ограничить соль" in ctx
    assert "Иванов И.И." in ctx


def test_recommend_lifestyle_calls_model_with_full_picture(monkeypatch, tmp_path):
    """recommend_lifestyle собирает контекст и шлёт его в RAG_LIFESTYLE_MODEL."""
    _client(monkeypatch, tmp_path)
    from botkin.db.repos import UserRepo
    with botkin.db.connection.get_conn() as conn:
        UserRepo(conn).get_or_create(int(TG))
        uid = UserRepo(conn).get_id(int(TG))
    _seed_reports(uid)

    import botkin.rag.recommend as recommend
    importlib.reload(recommend)
    captured: dict = {}

    def fake_chat(client, model, messages, num_predict, think=None):
        captured["model"] = model
        captured["messages"] = messages
        return _fake_response("## Образ жизни\nМеньше соли.")

    monkeypatch.setattr(recommend, "_chat", fake_chat)
    monkeypatch.setattr(recommend, "get_raw_client",
                        lambda timeout=None: SimpleNamespace(with_options=lambda **kw: None))
    monkeypatch.setattr(recommend.retriever, "search",
                        lambda *a, **kw: [])

    result = recommend.recommend_lifestyle(uid)
    assert result["answer"].startswith("## Образ жизни")
    assert result["model"] == recommend.RAG_LIFESTYLE_MODEL
    system = captured["messages"][0]["content"]
    assert "Взаимодействия" in system  # промпт lifestyle, а не обычный RAG
    user_msg = captured["messages"][1]["content"]
    assert "Гипертоническая болезнь" in user_msg
    assert "Лозартан" in user_msg


def test_api_lifestyle_endpoint(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    import botkin.rag.recommend as recommend
    monkeypatch.setattr(recommend, "recommend_lifestyle",
                        lambda uid, **kw: {"answer": "ok", "model": "m", "chunks": []})
    r = client.post("/api/rag/lifestyle", headers=_hdr())
    assert r.status_code == 200
    assert r.json()["answer"] == "ok"


def test_api_lifestyle_llm_down_returns_502(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    import botkin.rag.recommend as recommend

    def boom(uid, **kw):
        raise ConnectionError("ollama down")

    monkeypatch.setattr(recommend, "recommend_lifestyle", boom)
    r = client.post("/api/rag/lifestyle", headers=_hdr())
    assert r.status_code == 502
