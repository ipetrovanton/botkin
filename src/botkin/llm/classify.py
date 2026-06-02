"""Классификатор типа документа через VLM (дешёвый вызов на уменьшенной 1-й странице)."""
import logging
import time
from pathlib import Path

import instructor
from pydantic import BaseModel

from botkin.config import VLM_MODEL, VLM_TEMPERATURE, VLM_MAX_TOKENS, IMAGE_CLASSIFY_LONG_SIDE
from botkin.domain.models import ClassifyResult, DocType
from botkin.exceptions import ClassificationError
from botkin.llm.client import get_client, default_options
from botkin.llm.prompts import CLASSIFY_VLM_SYSTEM
from botkin.preprocess.images import prepare_images, to_base64_jpegs

log = logging.getLogger(__name__)


class ClassifySchema(BaseModel):
    doc_type: DocType
    confidence: float
    title: str | None = None
    clinic: str | None = None


def run_vlm(source_path: Path) -> ClassifyResult:
    """Классифицирует документ по уменьшенной первой странице."""
    t0 = time.perf_counter()
    log.info("[START_CLASSIFY] Doc: '%s' | Model: %s", source_path.name, VLM_MODEL)

    images = prepare_images(source_path, long_side=IMAGE_CLASSIFY_LONG_SIDE)
    b64 = to_base64_jpegs(images[:1])   # только первая страница
    client = get_client(temperature=VLM_TEMPERATURE, mode=instructor.Mode.JSON)

    content = [
        {"type": "text", "text": "Classify this medical document image."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64[0]}"}},
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
            extra_body={"options": default_options()},
        )
        elapsed = time.perf_counter() - t0
        usage = response._raw_response.usage
        log.info(
            "[SUCCESS_CLASSIFY] Doc: '%s' | Result: '%s' (conf=%.2f) | Elapsed: %.2fs | "
            "Prompt: %d t | Completion: %d t",
            source_path.name, response.doc_type, response.confidence,
            elapsed, usage.prompt_tokens, usage.completion_tokens,
        )
        return ClassifyResult(
            doc_type=response.doc_type, confidence=response.confidence,
            title=response.title, clinic=response.clinic,
        )
    except Exception as e:
        elapsed = time.perf_counter() - t0
        log.error("[FAILED_CLASSIFY] Doc: '%s' | Elapsed: %.2fs | Error: %s", source_path.name, elapsed, e)
        raise ClassificationError(f"Сбой классификации: {e}") from e
