"""Дымовой тест: research-чанки в ретриве + recommend() с веб-доступом на qwen3:8b."""
import re

from botkin.db.connection import get_conn, init_db
from botkin.db.repos import UserRepo
from botkin.rag import retriever, store
from botkin.rag.recommend import recommend

init_db()
with store.vec_conn() as conn:
    print("index_stats:", store.index_stats(conn))

hits = retriever.search("повышенные лимфоциты причины", sources=["research"], top_k=3)
print(f"\nresearch-хиты по 'повышенные лимфоциты': {len(hits)}")
for h in hits:
    print(f"  [{h['distance']:.3f}] {h['ref_key']}: {h['text'][:90]}")

with get_conn() as conn:
    uid = UserRepo(conn).get_or_create(113521070)

print("\n=== recommend(use_web=True) на qwen3:8b ===")
res = recommend(uid, "С чем могут быть связаны мои повышенные лимфоциты и моноциты?",
                model="qwen3:8b", use_web=True)
print("web_used:", res["web_used"], "| elapsed:", res["elapsed_s"], "s")
print("источники:", [c["source"] for c in res["chunks"]])
print("\n--- ОТВЕТ (первые 1200 симв.) ---")
clean = re.sub(r"<think>.*?</think>", "", res["answer"], flags=re.DOTALL).strip()
print(clean[:1200])
