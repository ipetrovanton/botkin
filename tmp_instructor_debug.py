from pathlib import Path

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

client = get_client(temperature=VLM_TEMP, mode=instructor.Mode.JSON)
b64_images = _pdf_to_base64_images(PDF_PATH)
content = [{"type": "text", "text": "Extract lab results from these document images."}]
for b64 in b64_images:
    content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
messages = [
    {"role": "system", "content": ANALYSIS_VLM_SYSTEM},
    {"role": "user", "content": content},
]
try:
    response = client.chat.completions.create(
        model=VLM_MODEL,
        messages=messages,
        response_model=LabResults,
        max_retries=2,
        max_tokens=VLM_MAX_TOKENS,
        extra_body={"options": {"num_ctx": VLM_NUM_CTX, "num_predict": VLM_NUM_PREDICT, "repeat_penalty": 1.2}},
    )
    print("OK", len(response.results))
except InstructorRetryException as exc:
    completion = exc.last_completion
    print("Finish reason:", completion.choices[0].finish_reason if completion else None)
    if completion:
        content_text = completion.choices[0].message.content or ""
        print("Raw length:", len(content_text))
        with open("tmp_instructor_raw.txt", "w", encoding="utf-8") as f:
            f.write(content_text)
    raise
