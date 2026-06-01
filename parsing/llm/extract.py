"""VLM-извлечение структурированных данных из медицинских документов."""
import base64
import logging
from pathlib import Path

from pydantic import BaseModel
from backend.contracts import LabResult, Prescription, DoctorReport
from backend.config import (
    VLM_MODEL, VLM_TEMPERATURE, VLM_NUM_CTX,
    VLM_MAX_TOKENS, PDF_SCALE_X, PDF_SCALE_Y, MAX_PAGES,
)
import instructor
import pymupdf

from .ollama_client import get_client
from .prompts import (
    ANALYSIS_VLM_SYSTEM, PRESCRIPTION_VLM_SYSTEM, DOCTOR_REPORT_VLM_SYSTEM,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Wrapper-модели для instructor (списки внутри ключа "results")
# ---------------------------------------------------------------------------
class LabResults(BaseModel):
    results: list[LabResult] = []

class Prescriptions(BaseModel):
    results: list[Prescription] = []

class DoctorReports(BaseModel):
    results: list[DoctorReport] = []


# ---------------------------------------------------------------------------
# PDF/изображение → base64 JPEG
# ---------------------------------------------------------------------------
def _pdf_to_base64_images(file_path: Path | str) -> list[str]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    b64_images: list[str] = []
    try:
        if path.suffix.lower() == ".pdf":
            doc = pymupdf.open(str(path))
            mat = pymupdf.Matrix(PDF_SCALE_X, PDF_SCALE_Y)
            for i, page in enumerate(doc):
                if i >= MAX_PAGES:
                    log.warning(
                        "PDF has more than %d pages, processing only first %d", MAX_PAGES, MAX_PAGES
                    )
                    break
                pix = page.get_pixmap(matrix=mat)
                b64_images.append(base64.b64encode(pix.tobytes("jpeg")).decode("utf-8"))
            doc.close()
        else:
            with open(path, "rb") as f:
                b64_images.append(base64.b64encode(f.read()).decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"Failed to convert {path} to base64: {e}") from e

    return b64_images


# ---------------------------------------------------------------------------
# VLM structured call
# ---------------------------------------------------------------------------
def _call_vlm(
    messages: list[dict],
    response_model: type[BaseModel],
    doc_name: str,
    doc_type: str,
    source_path: Path | None = None,
) -> BaseModel:
    import time

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
            "[SUCCESS_EXTRACT] Doc: '%s' | Type: '%s' | "
            "Elapsed: %.2fs | Prompt: %d t | Completion: %d t | Speed: %.1f t/s",
            doc_name, doc_type, elapsed, prompt_tokens, completion_tokens, speed,
        )

        # Сохраняем сырой ответ рядом с исходным файлом
        if source_path and source_path.exists():
            try:
                raw_text = raw_resp.choices[0].message.content or ""
                txt_path = source_path.with_suffix(".txt")
                txt_path.write_text(raw_text, encoding="utf-8")
            except Exception as e:
                log.error("Failed to write txt result: %s", e)

        return response

    except Exception as e:
        elapsed = time.perf_counter() - t0
        log.error(
            "[FAILED_EXTRACT] Doc: '%s' | Type: '%s' | Elapsed: %.2fs | Error: %s",
            doc_name, doc_type, elapsed, e,
        )
        raise


# ---------------------------------------------------------------------------
# Публичные функции извлечения
# ---------------------------------------------------------------------------
def run_analysis(source_path: Path) -> list[LabResult]:
    """Извлекает лабораторные показатели из документа (только VLM)."""
    b64_images = _pdf_to_base64_images(source_path)

    content: list[dict] = [{"type": "text", "text": "Extract lab results from these document images."}]
    for b64 in b64_images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    messages = [
        {"role": "system", "content": ANALYSIS_VLM_SYSTEM},
        {"role": "user", "content": content},
    ]
    response = _call_vlm(messages, LabResults, source_path.name, "analysis", source_path)
    return response.results


def run_prescription(source_path: Path) -> list[Prescription]:
    """Извлекает назначения лекарств из документа (только VLM)."""
    b64_images = _pdf_to_base64_images(source_path)

    content: list[dict] = [
        {"type": "text", "text": "Extract prescriptions from these document images."}
    ]
    for b64 in b64_images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    messages = [
        {"role": "system", "content": PRESCRIPTION_VLM_SYSTEM},
        {"role": "user", "content": content},
    ]
    response = _call_vlm(messages, Prescriptions, source_path.name, "prescription", source_path)
    return response.results


def run_doctor_report(source_path: Path) -> list[DoctorReport]:
    """Извлекает заключения врача из документа (только VLM)."""
    b64_images = _pdf_to_base64_images(source_path)

    content: list[dict] = [
        {"type": "text", "text": "Extract doctor reports from these document images."}
    ]
    for b64 in b64_images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    messages = [
        {"role": "system", "content": DOCTOR_REPORT_VLM_SYSTEM},
        {"role": "user", "content": content},
    ]
    response = _call_vlm(messages, DoctorReports, source_path.name, "doctor_report", source_path)
    return response.results