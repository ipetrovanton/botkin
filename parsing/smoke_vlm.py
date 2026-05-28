# parsing/smoke_vlm.py
import base64
import time
from pathlib import Path
import httpx
import os

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = "qwen3:14b"
VLM_MODEL = "qwen3-vl:8b"


def to_b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()


def smoke_vlm_swap(image_path: str) -> None:
    """Цикл: загружен LLM → выгружаем → грузим VLM → описание → выгружаем VLM → возвращаем LLM."""

    # 1. Прогреть LLM (в обычном режиме keep_alive=5m)
    print("→ Прогрев qwen3:14b...")
    httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": LLM_MODEL, "prompt": "Привет", "stream": False, "keep_alive": "5m"},
        timeout=120.0,
    )
    print("✅ LLM в VRAM")

    # 2. Выгрузить LLM (keep_alive=0 в запросе с пустым prompt'ом не работает — используй /api/unload через keep_alive=0 на следующем запросе)
    print("→ Выгрузка LLM (keep_alive=0)...")
    httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": LLM_MODEL, "prompt": "", "stream": False, "keep_alive": 0},
        timeout=30.0,
    )

    # 3. Загрузить VLM с картинкой
    print("→ Загрузка VLM и инференс по картинке...")
    t0 = time.perf_counter()
    r = httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": VLM_MODEL,
            "prompt": "Опиши, что на этом медицинском документе. Что за документ?",
            "images": [to_b64(image_path)],
            "stream": False,
            "keep_alive": 0,   # сразу выгрузить после ответа
        },
        timeout=120.0,
    )
    r.raise_for_status()
    vlm_elapsed = time.perf_counter() - t0
    print(f"✅ VLM ответ за {vlm_elapsed:.2f} с")
    print(f"Ответ VLM: {r.json()['response'][:200]}...")

    # 4. Возврат LLM
    print("→ Возврат qwen3:14b...")
    t0 = time.perf_counter()
    r2 = httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": LLM_MODEL, "prompt": "Сколько будет 2+2?", "stream": False, "keep_alive": "5m"},
        timeout=120.0,
    )
    r2.raise_for_status()
    back_elapsed = time.perf_counter() - t0
    print(f"✅ LLM обратно в VRAM за {back_elapsed:.2f} с")

    total = vlm_elapsed + back_elapsed
    print(f"\n📊 Полный цикл VLM swap: ~{total:.0f} с")
    assert total < 60, f"⚠️ VLM swap занимает {total:.0f}с — медленно, в плане ожидаем ~30-40 с"


if __name__ == "__main__":
    import sys
    img = sys.argv[1] if len(sys.argv) > 1 else "test_data/sample_analysis.jpg"
    smoke_vlm_swap(img)
