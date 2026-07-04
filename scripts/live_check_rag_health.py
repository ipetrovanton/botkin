"""Живая проверка итерации RAG+health (нужны Ollama и токены Garmin).

Запуск по шагам:
  uv run python scripts/live_check_rag_health.py index    # индексация справочников
  uv run python scripts/live_check_rag_health.py sync     # синк Garmin за 30 дней
  uv run python scripts/live_check_rag_health.py search   # смоук семантического поиска
  uv run python scripts/live_check_rag_health.py ask      # рекомендация LLM
"""
import sys
import time

from botkin.db.connection import get_conn, init_db
from botkin.db.repos import HealthRepo, UserRepo

DEMO_TG_ID = 113521070


def _user_id() -> int:
    with get_conn() as conn:
        return UserRepo(conn).get_or_create(DEMO_TG_ID)


def do_index():
    from botkin.rag.indexer import index_registries
    t0 = time.time()
    counts = index_registries()
    print(f"indexed: {counts} in {time.time() - t0:.0f}s")


def do_sync():
    from botkin.health import garmin
    from botkin.rag.indexer import index_health
    uid = _user_id()
    t0 = time.time()
    metrics, activities = garmin.fetch(uid, days=30,
                                       on_progress=lambda d, t: print(f"  {d}/{t}", end="\r"))
    with get_conn() as conn:
        repo = HealthRepo(conn, uid)
        repo.save_metrics(metrics)
        repo.save_activities(activities)
        repo.mark_synced("garmin")
        print(f"\nmetrics: {len(metrics)}, activities: {len(activities)}, "
              f"time {time.time() - t0:.0f}s")
        print("distinct:", repo.distinct_metrics())
    n = index_health(uid)
    print("health chunks indexed:", n)


def do_search():
    from botkin.rag import retriever
    uid = _user_id()
    for q, src in [("аторвастатин от холестерина", ["drugs"]),
                   ("гемоглобин в крови", ["analytes"]),
                   ("как я спал на этой неделе", ["health"])]:
        items = retriever.search(q, sources=src, user_id=uid, top_k=3)
        print(f"\nQ: {q}")
        for it in items:
            print(f"  [{it['distance']:.3f}] {it['ref_key']}: {it['text'][:100]}")


def do_ask():
    from botkin.rag.recommend import recommend
    uid = _user_id()
    result = recommend(uid, "Что означает мой уровень холестерина и пульс покоя? "
                            "На что обратить внимание?")
    print(result["answer"])
    print("\nchunks:", [c["ref_key"] for c in result["chunks"]])


if __name__ == "__main__":
    init_db()
    {"index": do_index, "sync": do_sync, "search": do_search, "ask": do_ask}[sys.argv[1]]()
