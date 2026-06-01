"""Классификатор типа документа через VLM."""
import logging
import time
from pathlib import Path

import instructor
from pydantic import BaseModel

from botkin.config import VLM_MODEL, VLM_TEMPERATURE, VLM_NUM_CTX, VLM_MAX_TOKENS
from botkin.domain.models import ClassifyResult, DocType
from botkin.exceptions import ClassificationError
from botkin.llm.client import get_client
from botkin.llm.extract import _pdf_to_base64_images
from botkin.llm.prompts import CLASSIFY_VLM_SYSTEM

log = logging.getLogger(__name__)


class ClassifySchema(BaseModel):
    doc_type: DocType
    confidence: float


def run_vlm(source_path: Path) -> ClassifyResult:
    """Классифицирует документ по первой странице через VLM."""
    t0 = time.perf_counter()
    log.info("[START_CLASSIFY] Doc: '%s' | Model: %s", source_path.name, VLM_MODEL)

    b64_images = _pdf_to_base64_images(source_path)
    client = get_client(temperature=VLM_TEMPERATURE, mode=instructor.Mode.JSON)

    content: list[dict] = [
        {"type": "text", "text": "Classify this medical document image."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_images[0]}"}},
    ]

    messages = [
        {"role": "system", "content": CLASSIFY_VLM_SYSTEM},
        {"role": "user", "content": content},
    ]

    try:
        response = client.chat.completions.create(
            model=VLM_MODEL,
            messages=messages,
            response_model=ClassifySchema,
            max_tokens=VLM_MAX_TOKENS,
            extra_body={
                "options": {
                    "num_ctx": VLM_NUM_CTX,
                    "repeat_penalty": 1.2,
                }
            },
        )
        elapsed = time.perf_counter() - t0
        raw_resp = response._raw_response
        prompt_tokens = raw_resp.usage.prompt_tokens
        completion_tokens = raw_resp.usage.completion_tokens
        speed = completion_tokens / elapsed if elapsed > 0 else 0.0

        log.info(
            "[SUCCESS_CLASSIFY] Doc: '%s' | Result: '%s' (conf=%.2f) | "
            "Elapsed: %.2fs | Prompt: %d t | Completion: %d t | Speed: %.1f t/s",
            source_path.name, response.doc_type, response.confidence,
            elapsed, prompt_tokens, completion_tokens, speed,
        )

        return ClassifyResult(doc_type=response.doc_type, confidence=response.confidence)

    except Exception as e:
        elapsed = time.perf_counter() - t0
        log.error("[FAILED_CLASSIFY] Doc: '%s' | Elapsed: %.2fs | Error: %s", source_path.name, elapsed, e)
        raise ClassificationError(f"Сбой классификации: {e}") from e