"""Сравнение: /v1 с extra_body.format vs /api/chat с format — что работает?"""
import openai, time, json, urllib.request

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
    {"role": "system", "content": "Верни JSON со схемой: {results: [{name, value, unit}]}"},
    {"role": "user", "content": "Ca 125: 8.13 Ед/мл\nHE4: 47.14 пмоль/л\nROMA: 6.31 %"},
]

# Test 1: /v1 with extra_body.format (как делает botkin)
print("=== /v1 + extra_body.format ===")
client = openai.OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
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
    print(f"Run {i+1}: {elapsed:.1f}s | len={len(content)} | {content[:120]}")

# Test 2: /api/chat with format (прямой Ollama API)
print("\n=== /api/chat + format ===")
for i in range(3):
    payload = {
        "model": "qwen3-vl:8b-instruct",
        "messages": messages,
        "stream": False,
        "format": schema,
        "options": {"temperature": 0.0, "num_predict": 2048, "num_ctx": 8192},
    }
    t0 = time.perf_counter()
    req = urllib.request.Request(
        "http://localhost:11434/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=120)
    data = json.loads(resp.read())
    elapsed = time.perf_counter() - t0
    content = data.get("message", {}).get("content", "")
    print(f"Run {i+1}: {elapsed:.1f}s | len={len(content)} | {content[:120]}")
