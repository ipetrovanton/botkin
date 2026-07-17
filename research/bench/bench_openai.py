"""Диагностика: прямой /v1/chat/completions через OpenAI SDK как делает instructor."""
import openai, time, json

client = openai.OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

schema = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "number"},
                    "unit": {"type": "string"},
                },
                "required": ["name", "value"],
            },
        },
    },
    "required": ["results"],
}

messages = [
    {"role": "system", "content": "Ты — точный OCR медицинских таблиц. Верни JSON."},
    {"role": "user", "content": "Извлеки показатели:\n\nCa 125: 8.13 Ед/мл\nHE4: 47.14 пмоль/л\nROMA: 6.31 %"},
]

# Test 1: with format (structured_output=True equivalent)
print("=== With format (structured_output=True) ===")
for i in range(3):
    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model="qwen3-vl:8b-instruct",
        messages=messages,
        max_tokens=2048,
        extra_body={"options": {"temperature": 0.0, "num_predict": 2048, "num_ctx": 8192},
                     "format": schema},
    )
    elapsed = time.perf_counter() - t0
    content = resp.choices[0].message.content or ""
    print(f"Run {i+1}: {elapsed:.1f}s | content_len={len(content)} | {content[:80]}")

# Test 2: without format (structured_output=False equivalent)
print("\n=== Without format (structured_output=False) ===")
for i in range(3):
    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model="qwen3-vl:8b-instruct",
        messages=messages,
        max_tokens=2048,
        extra_body={"options": {"temperature": 0.0, "num_predict": 2048, "num_ctx": 8192}},
    )
    elapsed = time.perf_counter() - t0
    content = resp.choices[0].message.content or ""
    print(f"Run {i+1}: {elapsed:.1f}s | content_len={len(content)} | {content[:80]}")
