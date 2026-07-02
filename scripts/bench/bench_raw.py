"""Прямой запрос к Ollama /api/chat без instructor — диагностика пустых ответов."""
import urllib.request, json, time

url = "http://localhost:11434/api/chat"
payload = {
    "model": "qwen3-vl:8b-instruct",
    "messages": [
        {"role": "system", "content": "Ты — точный OCR медицинских таблиц. Верни JSON."},
        {"role": "user", "content": "Извлеки показатели анализов из текста:\n\nАнтиген аденогенных раков Ca 125 в крови: 8.13 Ед/мл\nСекреторный белок эпидидимиса человека HE4 в крови: 47.14 пмоль/л\nРиск рака яичников в пременопаузе (алгоритм ROMA): 6.31 %"},
    ],
    "stream": False,
    "format": {
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
    },
    "options": {
        "temperature": 0.0,
        "num_predict": 2048,
        "num_ctx": 8192,
    },
}

for i in range(5):
    t0 = time.perf_counter()
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=120)
    data = json.loads(resp.read())
    elapsed = time.perf_counter() - t0
    content = data.get("message", {}).get("content", "")
    thinking = data.get("message", {}).get("thinking", "")
    eval_count = data.get("eval_count", 0)
    print(f"Run {i+1}: {elapsed:.1f}s | eval_count={eval_count} | content_len={len(content)} | thinking_len={len(thinking)}")
    if len(content) < 50:
        print(f"  content: {content[:200]}")
    else:
        print(f"  content[:100]: {content[:100]}")
