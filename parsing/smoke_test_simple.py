# parsing/smoke_test_simple.py
import time
import httpx
import os

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

def smoke_chat_simple(model: str) -> None:
    """Замер латентности генерации для указанной модели."""
    prompt = "Перечисли три нормальных показателя гемоглобина у взрослой женщины. Только цифры с единицами."

    print(f"Testing model: {model}")
    t0 = time.perf_counter()
    
    try:
        response = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": model,
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
        print(f"Response:\n{data['response'][:200]}...\n")
        
        return tokens_per_sec

    except Exception as e:
        print(f"Error: {e}")
        return 0

if __name__ == "__main__":
    # Test with tinyllama first
    speed = smoke_chat_simple("tinyllama:latest")
    if speed > 0:
        print("✅ TinyLlama test passed")
        
        # Test with qwen3:14b
        print("\n" + "="*50)
        speed = smoke_chat_simple("qwen3:14b")
        if speed >= 20:
            print("✅ Qwen3-14B test passed")
        else:
            print(f"⚠️ Qwen3-14B throughput {speed:.1f} t/s ниже целевого 20 t/s")
    else:
        print("❌ TinyLlama test failed")
