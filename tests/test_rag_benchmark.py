"""Тесты RAG: API research-update/status, API benchmark, бенчмарк-логика."""
import importlib

import botkin.config
import botkin.db.connection


def _client(monkeypatch, tmp_path):
    db = tmp_path / "rag7.db"
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
    return {"X-Telegram-User-Id": "999"}


# ===== API research/update + status =====

def test_research_update_starts(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/rag/research/update", headers=_hdr())
    assert r.status_code == 200
    assert r.json()["status"] == "started"


def test_research_status_idle(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    # Сбрасываем глобальное состояние — предыдущий тест мог запустить фоновую задачу
    import botkin.api.routes.rag as rag_route
    with rag_route._research_lock:
        rag_route._research_state = {"state": "idle"}
    r = client.get("/api/rag/research/status")
    assert r.status_code == 200
    assert r.json()["state"] == "idle"


def test_research_update_requires_auth(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post("/api/rag/research/update").status_code == 401


# ===== API benchmark =====

def test_benchmark_requires_auth(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post("/api/rag/benchmark", json={"models": ["bge-m3"]}).status_code == 401


def test_benchmark_validation_empty_models(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/rag/benchmark", json={"models": []}, headers=_hdr())
    assert r.status_code == 422


# ===== Бенчмарк-логика (мок Ollama) =====

def test_benchmark_golden_set_structure():
    from botkin.rag.benchmark import _GOLDEN_SET
    assert len(_GOLDEN_SET) >= 3
    for q in _GOLDEN_SET:
        assert q.query
        assert q.expected_ref_keys
        assert isinstance(q.expected_ref_keys, list)


def test_benchmark_format_results():
    from botkin.rag.benchmark import ModelResult, format_results
    results = [
        ModelResult(model="bge-m3", hit_rate=0.8, mrr=0.65, avg_distance=0.12),
        ModelResult(model="nomic-embed", hit_rate=0.6, mrr=0.45, avg_distance=0.18),
    ]
    text = format_results(results)
    assert "bge-m3" in text
    assert "nomic-embed" in text
    assert "hit_rate" in text


def test_benchmark_empty_corpus(monkeypatch, tmp_path):
    """Бенчмарк на пустом rag_chunks возвращает пустой список."""
    db = tmp_path / "bench_empty.db"
    monkeypatch.setenv("SQLITE_PATH", str(db))
    importlib.reload(botkin.config)
    importlib.reload(botkin.db.connection)
    botkin.db.connection.init_db()

    from botkin.rag.benchmark import run_benchmark
    results = run_benchmark(["bge-m3"])
    assert results == []
