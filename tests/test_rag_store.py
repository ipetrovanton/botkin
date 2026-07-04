"""Тесты RAG: sqlite-vec хранилище (без Ollama — вектора рукотворные) и чанкование."""
from botkin.rag import store
from botkin.rag.indexer import analyte_chunk, drug_chunk, health_week_chunks


def _vec(seed: float) -> list[float]:
    """Детерминированный вектор нужной размерности с «сигналом» в первой координате."""
    v = [0.001] * store.DIM
    v[0] = seed
    return v


def test_upsert_and_search(set_test_db):
    items = [
        {"ref_key": "drug:аторвастатин", "text": "Аторвастатин, статин"},
        {"ref_key": "drug:парацетамол", "text": "Парацетамол, анальгетик"},
    ]
    with store.vec_conn() as conn:
        store.upsert_chunks(conn, "drugs", items, [_vec(1.0), _vec(-1.0)])
        results = store.search(conn, _vec(0.9), top_k=1)
    assert results[0]["ref_key"] == "drug:аторвастатин"
    assert results[0]["source"] == "drugs"


def test_upsert_idempotent_by_ref_key(set_test_db):
    item = {"ref_key": "drug:аспирин", "text": "v1"}
    with store.vec_conn() as conn:
        store.upsert_chunks(conn, "drugs", [item], [_vec(1.0)])
        store.upsert_chunks(conn, "drugs", [{**item, "text": "v2"}], [_vec(1.0)])
        stats = store.index_stats(conn)
        results = store.search(conn, _vec(1.0), top_k=1)
    assert stats["chunks"]["drugs"] == 1
    assert stats["vectors"] == 1
    assert results[0]["text"] == "v2"


def test_search_private_health_chunks_scoped(set_test_db):
    """health-чанки видны только владельцу, справочники — всем."""
    with store.vec_conn() as conn:
        store.upsert_chunks(conn, "health", [
            {"ref_key": "health:1:2026-W27", "text": "пульс 60", "user_id": 1},
        ], [_vec(1.0)])
        store.upsert_chunks(conn, "drugs", [
            {"ref_key": "drug:x", "text": "препарат X"},
        ], [_vec(0.99)])
        mine = store.search(conn, _vec(1.0), user_id=1, top_k=5)
        other = store.search(conn, _vec(1.0), user_id=2, top_k=5)
    assert {r["ref_key"] for r in mine} == {"health:1:2026-W27", "drug:x"}
    assert {r["ref_key"] for r in other} == {"drug:x"}


def test_search_filter_by_source(set_test_db):
    with store.vec_conn() as conn:
        store.upsert_chunks(conn, "drugs", [{"ref_key": "d:1", "text": "d"}], [_vec(1.0)])
        store.upsert_chunks(conn, "analytes", [{"ref_key": "a:1", "text": "a"}], [_vec(1.0)])
        only_drugs = store.search(conn, _vec(1.0), sources=["drugs"], top_k=5)
    assert {r["source"] for r in only_drugs} == {"drugs"}


def test_drug_chunk_text():
    chunk = drug_chunk({"name": "Липримар", "type": "trade", "mnn": "Аторвастатин",
                        "statuses": ["active", "modified"]})
    assert chunk["ref_key"] == "drug:липримар"
    assert "торговое название" in chunk["text"]
    assert "Аторвастатин" in chunk["text"]
    assert "активен" in chunk["text"]
    assert "изменялась" not in chunk["text"]  # технический статус не показываем


def test_drug_chunk_excluded_status():
    chunk = drug_chunk({"name": "Анальгин-Х", "type": "trade", "statuses": ["excluded"]})
    assert "исключён из реестра" in chunk["text"]


def test_analyte_chunk_text():
    chunk = analyte_chunk({"name": "Гемоглобин", "synonyms": ["HGB", "Hb"],
                           "units": ["г/л"], "group": "Гематология"})
    assert chunk["ref_key"] == "analyte:гемоглобин"
    for fragment in ("Гемоглобин", "HGB", "г/л", "Гематология"):
        assert fragment in chunk["text"]


def test_health_week_chunks(set_test_db):
    import datetime as dt
    from botkin.db.connection import get_conn
    from botkin.db.repos import HealthRepo, UserRepo

    today = str(dt.date.today())
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(42)
        repo = HealthRepo(conn, uid)
        repo.save_metrics([
            {"provider": "garmin", "metric": "resting_heart_rate",
             "taken_at": today, "value_num": 58.0, "unit": "уд/мин"},
            {"provider": "garmin", "metric": "sleep_seconds",
             "taken_at": today, "value_num": 7.2 * 3600, "unit": "с"},
        ])
        chunks = health_week_chunks(repo, weeks=2)
    assert len(chunks) == 1
    assert chunks[0]["user_id"] == uid
    assert "пульс покоя" in chunks[0]["text"]
    assert "7.2 ч" in chunks[0]["text"]
