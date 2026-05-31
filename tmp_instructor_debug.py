import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path("botkin-core").absolute()))
sys.path.insert(0, str(Path("C:/Sandbox/botkin").absolute()))

print("[Step 1] Loading imports...")
from backend.contracts import LabResult
from backend.config import (
    VLM_MODEL,
    VLM_TEMP,
    VLM_NUM_CTX,
    VLM_NUM_PREDICT,
    VLM_MAX_TOKENS,
)
from instructor.exceptions import InstructorRetryException
from parsing.llm.extract import _pdf_to_base64_images, ANALYSIS_VLM_SYSTEM, LabResults
from parsing.llm.ollama_client import get_client

PDF_PATH = Path(r"test-dataset/datasets/medknow-test/raw/user_samples/sample_020.pdf")

import instructor

print("[Step 2] Initializing client...")
client = get_client(temperature=VLM_TEMP, mode=instructor.Mode.JSON)

print("[Step 3] Converting PDF...")
b64_images = _pdf_to_base64_images(PDF_PATH)
content = [{"type": "text", "text": "Extract lab results from these document images."}]
for b64 in b64_images:
    content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
messages = [
    {"role": "system", "content": ANALYSIS_VLM_SYSTEM},
    {"role": "user", "content": content},
]

print("[Step 4] Sending structured completions to Ollama...")
start = time.perf_counter()
try:
    response = client.chat.completions.create(
        model=VLM_MODEL,
        messages=messages,
        response_model=LabResults,
        max_retries=2,
        max_tokens=VLM_MAX_TOKENS,
        extra_body={"options": {"num_ctx": VLM_NUM_CTX, "num_predict": VLM_NUM_PREDICT, "repeat_penalty": 1.2}},
    )
    elapsed = time.perf_counter() - start
    print(f"[Step 4 Done] Success! Extracted {len(response.results)} metrics in {elapsed:.2f} seconds.")
    with open("vlm_extracted_results.txt", "w", encoding="utf-8") as f:
        f.write(f"Success! Extracted {len(response.results)} metrics in {elapsed:.2f} seconds.\n\n")
        for idx, r in enumerate(response.results):
            line = f"  [{idx+1}] {r.analyte_name} = {r.value_num} {r.unit or ''}\n"
            f.write(line)
            sys.stdout.buffer.write(line.encode("utf-8"))
            sys.stdout.buffer.flush()
except InstructorRetryException as exc:
    completion = exc.last_completion
    print("Finish reason:", completion.choices[0].finish_reason if completion else None)
    if completion:
        content_text = completion.choices[0].message.content or ""
        print("Raw length:", len(content_text))
        with open("tmp_instructor_raw.txt", "w", encoding="utf-8") as f:
            f.write(content_text)
    raise
