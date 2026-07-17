"""Тест: instructor Mode.JSON_SCHEMA + Ollama format — что работает?"""
import instructor, openai, time, json
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional

class _RawRow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    parameter: Optional[str] = None
    value: Optional[str | float | int] = None
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    comment: Optional[str] = None

class _RawTest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    test_name: Optional[str] = None
    results: list[_RawRow] = []

class RawAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tests: list[_RawTest] = []
    results: list[_RawRow] = []

messages = [
    {"role": "system", "content": "Ты — медицинский ассистент. Структурируй показатели анализов в JSON."},
    {"role": "user", "content": "Ca 125: 8.13 Ед/мл\nHE4: 47.14 пмоль/л\nROMA: 6.31 %"},
]

schema = RawAnalysis.model_json_schema()
options = {"temperature": 0.0, "num_predict": 2048, "num_ctx": 8192, "keep_alive": "30m"}

# Test 1: Mode.JSON + format (текущий подход botkin)
print("=== Mode.JSON + format ===")
client = instructor.from_openai(
    openai.OpenAI(base_url="http://localhost:11434/v1", api_key="ollama"),
    mode=instructor.Mode.JSON,
)
for i in range(3):
    t0 = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model="qwen3-vl:8b-instruct",
            messages=messages,
            response_model=RawAnalysis,
            max_retries=1,
            max_tokens=2048,
            extra_body={"options": options, "format": schema},
        )
        elapsed = time.perf_counter() - t0
        print(f"Run {i+1}: {elapsed:.1f}s | tests={len(resp.tests)} results={len(resp.results)}")
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"Run {i+1}: {elapsed:.1f}s | ERROR: {type(e).__name__}: {str(e)[:100]}")

# Test 2: Mode.JSON_SCHEMA + format
print("\n=== Mode.JSON_SCHEMA + format ===")
client2 = instructor.from_openai(
    openai.OpenAI(base_url="http://localhost:11434/v1", api_key="ollama"),
    mode=instructor.Mode.JSON_SCHEMA,
)
for i in range(3):
    t0 = time.perf_counter()
    try:
        resp = client2.chat.completions.create(
            model="qwen3-vl:8b-instruct",
            messages=messages,
            response_model=RawAnalysis,
            max_retries=1,
            max_tokens=2048,
            extra_body={"options": options, "format": schema},
        )
        elapsed = time.perf_counter() - t0
        print(f"Run {i+1}: {elapsed:.1f}s | tests={len(resp.tests)} results={len(resp.results)}")
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"Run {i+1}: {elapsed:.1f}s | ERROR: {type(e).__name__}: {str(e)[:100]}")

# Test 3: Mode.JSON без format (только prompt-only)
print("\n=== Mode.JSON без format ===")
client3 = instructor.from_openai(
    openai.OpenAI(base_url="http://localhost:11434/v1", api_key="ollama"),
    mode=instructor.Mode.JSON,
)
for i in range(3):
    t0 = time.perf_counter()
    try:
        resp = client3.chat.completions.create(
            model="qwen3-vl:8b-instruct",
            messages=messages,
            response_model=RawAnalysis,
            max_retries=1,
            max_tokens=2048,
            extra_body={"options": options},
        )
        elapsed = time.perf_counter() - t0
        print(f"Run {i+1}: {elapsed:.1f}s | tests={len(resp.tests)} results={len(resp.results)}")
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"Run {i+1}: {elapsed:.1f}s | ERROR: {type(e).__name__}: {str(e)[:100]}")
