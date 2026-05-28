# parsing/smoke_test.py
import time
import httpx
import os

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = "qwen3:14b"


def smoke_chat() -> None:
    """Замер латентности генерации Qwen3-14B."""
    prompt = (
        "Перечисли три нормальных показателя гемоглобина у взрослой женщины "
        "и три у взрослого мужчины. Только цифры с единицами."
    )

    t0 = time.perf_counter()
    response = httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": LLM_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 4096},
        },
        timeout=120.0,
    )
    response.raise_for_status()
    elapsed = time.perf_counter() - t0
    data = response.json()

    eval_count = data.get("eval_count", 0)
    eval_duration_ns = data.get("eval_duration", 1)
    tokens_per_sec = eval_count / (eval_duration_ns / 1e9)

    print(f"Total elapsed: {elapsed:.2f} с")
    print(f"Tokens generated: {eval_count}")
    print(f"Generation speed: {tokens_per_sec:.1f} t/s")
    print(f"Response:\n{data['response']}\n")

    assert tokens_per_sec >= 20, f"⚠️ Throughput {tokens_per_sec:.1f} t/s ниже целевого 30 t/s"


if __name__ == "__main__":
    smoke_chat()
