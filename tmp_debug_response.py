import sys
from pathlib import Path
sys.path.insert(0, str(Path("botkin-core").absolute()))
sys.path.insert(0, str(Path("C:/Sandbox/botkin").absolute()))

import time
import argparse
from parsing.llm.extract import _pdf_to_base64_images, ANALYSIS_VLM_SYSTEM
from backend.config import VLM_MODEL, VLM_TEMP, VLM_NUM_CTX, VLM_NUM_PREDICT, VLM_MAX_TOKENS
from parsing.llm.ollama_client import get_raw_client

parser = argparse.ArgumentParser(description="Stream raw VLM response for debugging")
parser.add_argument("--file", type=Path, default=Path("test-dataset/datasets/medknow-test/raw/user_samples/sample_001.pdf"), help="Путь к PDF или изображению")
parser.add_argument("--log", type=Path, default=None, help="Файл для сохранения сырых токенов")
args = parser.parse_args()

pdf_path = args.file
images = _pdf_to_base64_images(pdf_path)
content = [{"type": "text", "text": "Extract lab results from these document images."}]
for b64 in images:
    content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

messages = [
    {"role": "system", "content": ANALYSIS_VLM_SYSTEM},
    {"role": "user", "content": content},
]

client = get_raw_client()
stream = client.chat.completions.create(
    model=VLM_MODEL,
    messages=messages,
    temperature=VLM_TEMP,
    max_tokens=VLM_MAX_TOKENS,
    extra_body={"options": {"num_ctx": VLM_NUM_CTX, "num_predict": VLM_NUM_PREDICT, "repeat_penalty": 1.2}},
    stream=True,
)

chunks = []
token_counter = 0
start = time.perf_counter()
for event in stream:
    delta = event.choices[0].delta
    fragment = ""
    if delta.content:
        fragment = "".join(part.text for part in delta.content if part.text)
    elif getattr(delta, "reasoning", None):
        fragment = delta.reasoning
    if fragment:
        token_counter += 1
        chunks.append(fragment)
        elapsed = time.perf_counter() - start
        print(f"[{elapsed:6.2f}s] TOKEN #{token_counter}: {fragment!r}")
        sys.stdout.flush()

full_text = "".join(chunks)
print("--- FINAL TEXT (truncated to 500 chars) ---")
print(full_text[:500])

Path("logs").mkdir(exist_ok=True)
output_file = args.log or Path(f"logs/{pdf_path.stem}_vlm_raw.json")
output_file.write_text(full_text, encoding="utf-8")
print(f"saved content to {output_file}")
