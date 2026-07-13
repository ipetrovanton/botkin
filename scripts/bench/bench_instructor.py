"""Диагностика: что отправляет instructor в Ollama?"""
import instructor, openai, time, json, logging
from pydantic import BaseModel, Field
from typing import Optional

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("openai").setLevel(logging.DEBUG)

class RawAnalysis(BaseModel):
    tests: list = Field(default_factory=list)
    results: list = Field(default_factory=list)

client = instructor.from_openai(
    openai.OpenAI(base_url="http://localhost:11434/v1", api_key="ollama"),
    mode=instructor.Mode.JSON,
)

schema = RawAnalysis.model_json_schema()
options = {"temperature": 0.0, "num_predict": 2048, "num_ctx": 8192, "keep_alive": "30m"}
extra_body = {"options": options, "format": schema}

messages = [
    {"role": "system", "content": "Верни JSON: {tests: [{test_name, results: [{parameter, value, unit, reference_range, comment}]}], results: [{parameter, value, unit, reference_range, comment}]}"},
    {"role": "user", "content": "Ca 125: 8.13 Ед/мл\nHE4: 47.14 пмоль/л\nROMA: 6.31 %"},
]

print("=== instructor call ===")
try:
    resp = client.chat.completions.create(
        model="qwen3-vl:8b-instruct",
        messages=messages,
        response_model=RawAnalysis,
        max_retries=1,
        max_tokens=2048,
        extra_body=extra_body,
    )
    print(f"Success: tests={len(resp.tests)} results={len(resp.results)}")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
