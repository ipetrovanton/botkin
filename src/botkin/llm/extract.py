"""VLM-извлечение структурированных данных из медицинских документов."""
import logging
import time
from pathlib import Path

import instructor
from pydantic import BaseModel

from botkin.config import VLM_MODEL, VLM_TEMPERATURE, VLM_MAX_TOKENS, IMAGE_EXTRACT_LONG_SIDE
from botkin.domain.models import LabResult, Prescription, DoctorReport
from botkin.exceptions import ExtractionError
from botkin.llm.client import get_client, default_options
from botkin.llm.prompts import (
    ANALYSIS_VLM_SYSTEM, PRESCRIPTION_VLM_SYSTEM, DOCTOR_REPORT_VLM_SYSTEM,
)
from botkin.preprocess.images import prepare_images, to_base64_jpegs

log = logging.getLogger(__name__)


class LabResults(BaseModel):
    results: list[LabResult] = []


class Prescriptions(BaseModel):
    results: list[Prescription] = []


class DoctorReports(BaseModel):
    results: list[DoctorReport] = []


def _build_messages(system_prompt: str, instruction: str, source_path: Path) -> list[dict]:
    b64_images = to_base64_jpegs(prepare_images(
        source_path,
        long_side=IMAGE_EXTRACT_LONG_SIDE,
        upscale=True, deskew=True, enhance=True,
    ))
    content: list[dict] = [{"type": "text", "text": instruction}]
    for b64 in b64_images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]


def _call_vlm(messages: list[dict], response_model: type[BaseModel], doc_name: str, doc_type: str) -> BaseModel:
    t0 = time.perf_counter()
    log.info("[START_EXTRACT] Doc: '%s' | Type: '%s' | Model: %s", doc_name, doc_type, VLM_MODEL)
    client = get_client(temperature=VLM_TEMPERATURE, mode=instructor.Mode.JSON)
    try:
        response = client.chat.completions.create(
            model=VLM_MODEL,
            messages=messages,
            response_model=response_model,
            max_retries=2,
            max_tokens=VLM_MAX_TOKENS,
            extra_body={"options": default_options()},
        )
        elapsed = time.perf_counter() - t0
        usage = response._raw_response.usage
        log.info(
            "[SUCCESS_EXTRACT] Doc: '%s' | Type: '%s' | Elapsed: %.2fs | Prompt: %d t | Completion: %d t",
            doc_name, doc_type, elapsed, usage.prompt_tokens, usage.completion_tokens,
        )
        return response
    except Exception as e:
        elapsed = time.perf_counter() - t0
        log.error("[FAILED_EXTRACT] Doc: '%s' | Type: '%s' | Elapsed: %.2fs | Error: %s", doc_name, doc_type, elapsed, e)
        raise ExtractionError(f"Сбой извлечения ({doc_type}): {e}") from e


def run_analysis(source_path: Path) -> list[LabResult]:
    messages = _build_messages(ANALYSIS_VLM_SYSTEM, "Extract lab results from these document images.", source_path)
    return _call_vlm(messages, LabResults, source_path.name, "analysis").results


def run_prescription(source_path: Path) -> list[Prescription]:
    messages = _build_messages(PRESCRIPTION_VLM_SYSTEM, "Extract prescriptions from these document images.", source_path)
    return _call_vlm(messages, Prescriptions, source_path.name, "prescription").results


def run_doctor_report(source_path: Path) -> list[DoctorReport]:
    messages = _build_messages(DOCTOR_REPORT_VLM_SYSTEM, "Extract doctor reports from these document images.", source_path)
    return _call_vlm(messages, DoctorReports, source_path.name, "doctor_report").results
