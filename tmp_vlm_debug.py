import sys
from pathlib import Path
sys.path.insert(0, str(Path("botkin-core").absolute()))
sys.path.insert(0, str(Path("C:/Sandbox/botkin").absolute()))

from openai import OpenAI
from parsing.llm.extract import _pdf_to_base64_images, ANALYSIS_VLM_SYSTEM
from backend.config import (
    VLM_MODEL,
    VLM_TEMP,
    VLM_NUM_CTX,
    VLM_NUM_PREDICT,
    VLM_MAX_TOKENS,
)

PDF_PATH = Path(r"test-dataset/datasets/medknow-test/raw/user_samples/sample_020.pdf")

images = _pdf_to_base64_images(PDF_PATH)
from parsing.llm.ollama_client import OLLAMA_URL
client = OpenAI(base_url=f"{OLLAMA_URL}/v1", api_key="ollama", timeout=600)

content = [{"type": "text", "text": "Extract lab results from these document images."}]
for b64 in images:
    content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

messages = [
    {"role": "system", "content": ANALYSIS_VLM_SYSTEM},
    {"role": "user", "content": content},
]

resp = client.chat.completions.create(
    model=VLM_MODEL,
    messages=messages,
    temperature=VLM_TEMP,
    max_tokens=VLM_MAX_TOKENS,
    extra_body={"options": {"num_ctx": VLM_NUM_CTX, "num_predict": VLM_NUM_PREDICT, "repeat_penalty": 1.2}},
)
choice = resp.choices[0]
print("finish:", choice.finish_reason)
print("length:", len(choice.message.content or ""))
with open("tmp_vlm_resp.txt", "w", encoding="utf-8") as f:
    f.write(choice.message.content or "")
