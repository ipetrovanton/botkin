# parsing/smoke_embed.py
import time
import httpx
import os

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")


def smoke_embed() -> None:
    chunks = [
        "Гемоглобин 145 г/л, норма 120-160 г/л.",
        "Холестерин общий 6.2 ммоль/л, целевой <5.2.",
        "Принимать аторвастатин 20 мг 1 раз в день вечером в течение 30 дней.",
    ] * 10  # 30 чанков для оценки throughput

    t0 = time.perf_counter()
    vectors = []
    for chunk in chunks:
        r = httpx.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": "bge-m3", "prompt": chunk},
            timeout=60.0,
        )
        r.raise_for_status()
        vec = r.json()["embedding"]
        vectors.append(vec)

    elapsed = time.perf_counter() - t0
    cps = len(chunks) / elapsed * 60   # chunks per minute

    print(f"Chunks: {len(chunks)}, dim={len(vectors[0])}")
    print(f"Elapsed: {elapsed:.2f} с")
    print(f"Throughput: {cps:.0f} chunks/min")
    assert len(vectors[0]) == 1024, "BGE-M3 должна давать 1024-dim"
    # NaN-проверка (известная проблема ollama#13572)
    import math
    for v in vectors:
        assert all(not math.isnan(x) for x in v), "❌ NaN в embedding"
    print("✅ embeddings ok, NaN не найдено")


if __name__ == "__main__":
    smoke_embed()
