"""Pipeline обработки документа: classify → extract → persist."""
import asyncio
import logging
from pathlib import Path

from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo
from botkin.domain.models import LabResult, Prescription, DoctorReport
from botkin.exceptions import ClassificationError, ExtractionError
from botkin.llm import classify, extract
from botkin.pipeline.notifications import (
    classify_failed, document_processed, extract_failed, notify_user, pipeline_failed,
)

log = logging.getLogger("botkin.pipeline")

LLM_SEMAPHORE = asyncio.Semaphore(1)


async def process_document(document_id: int, telegram_user_id: int) -> None:
    """Полный pipeline: classify → extract → persist. Точка входа из API."""
    try:
        await _run(document_id, telegram_user_id)
    except Exception as e:
        log.exception("Global pipeline failure for %d", document_id)
        _mark_failed(document_id)
        await notify_user(telegram_user_id, pipeline_failed(document_id, str(e)))


async def _run(document_id: int, telegram_user_id: int) -> None:
    with get_conn() as conn:
        doc = conn.execute(
            "SELECT id, user_id, source_path FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()

    if not doc:
        log.error("Document %d not found", document_id)
        return

    user_id = doc["user_id"]
    source_path = Path(doc["source_path"])

    # ── 1. Статус: processing ──────────────────────────────────────────────
    with get_conn() as conn:
        DocumentRepo(conn, user_id).set_status(document_id, "processing")

    # ── 2. Classify (VLM) ──────────────────────────────────────────────────
    async with LLM_SEMAPHORE:
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, classify.run_vlm, source_path,
            )
        except ClassificationError as e:
            _mark_failed(document_id)
            await notify_user(telegram_user_id, classify_failed(document_id, str(e)))
            return

    doc_type = result.doc_type
    log.info("Doc %d classified as %s (conf=%.2f)", document_id, doc_type, result.confidence)

    with get_conn() as conn:
        DocumentRepo(conn, user_id).set_doc_type(document_id, doc_type)

    # ── 3. Extract (VLM) ───────────────────────────────────────────────────
    async with LLM_SEMAPHORE:
        try:
            if doc_type == "analysis":
                items: list[LabResult] = await asyncio.get_event_loop().run_in_executor(
                    None, extract.run_analysis, source_path,
                )
                _persist_lab(document_id, user_id, items)

            elif doc_type == "prescription":
                items: list[Prescription] = await asyncio.get_event_loop().run_in_executor(
                    None, extract.run_prescription, source_path,
                )
                _persist_prescription(document_id, user_id, items)

            elif doc_type == "doctor_report":
                items: list[DoctorReport] = await asyncio.get_event_loop().run_in_executor(
                    None, extract.run_doctor_report, source_path,
                )
                _persist_doctor_report(document_id, user_id, items)

            else:
                log.info("Doc %d type=%s — extract пропускаем", document_id, doc_type)

        except ExtractionError as e:
            _mark_failed(document_id)
            await notify_user(telegram_user_id, extract_failed(document_id, str(e)))
            return

    # ── 4. Финал ───────────────────────────────────────────────────────────
    with get_conn() as conn:
        DocumentRepo(conn, user_id).set_status(document_id, "extracted")

    log.info("Doc %d processed", document_id)
    await notify_user(telegram_user_id, document_processed(document_id, doc_type))


# ── Хелперы ────────────────────────────────────────────────────────────────────

def _mark_failed(document_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE documents SET status = 'failed' WHERE id = ?", (document_id,))
        conn.commit()


# ── Persist ────────────────────────────────────────────────────────────────────

def _persist_lab(document_id: int, user_id: int, items: list[LabResult]) -> None:
    with get_conn() as conn:
        for item in items:
            conn.execute(
                """INSERT INTO lab_results(document_id, user_id, analyte_code, analyte_name,
                   value_num, value_text, unit, ref_low, ref_high, taken_at, source_table_cell)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (document_id, user_id, item.analyte_code, item.analyte_name,
                 item.value_num, item.value_text, item.unit,
                 item.ref_low, item.ref_high,
                 item.taken_at.isoformat() if item.taken_at else None,
                 item.source_table_cell),
            )
        conn.commit()


def _persist_prescription(document_id: int, user_id: int, items: list[Prescription]) -> None:
    with get_conn() as conn:
        for item in items:
            conn.execute(
                """INSERT INTO prescriptions(document_id, user_id, drug_mnn, drug_trade,
                   dose, frequency, duration_days, prescribed_at, doctor_name, form_107_1u_flag)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (document_id, user_id, item.drug_mnn, item.drug_trade,
                 item.dose, item.frequency, item.duration_days,
                 item.prescribed_at.isoformat() if item.prescribed_at else None,
                 item.doctor_name, item.form_107_1u_flag),
            )
        conn.commit()


def _persist_doctor_report(document_id: int, user_id: int, items: list[DoctorReport]) -> None:
    import json

    with get_conn() as conn:
        for item in items:
            conn.execute(
                """INSERT INTO doctor_reports(document_id, user_id, diagnosis,
                   recommendations_json, complaints_json, anamnesis, medications_json,
                   visit_date, doctor_name, department)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (document_id, user_id, item.diagnosis,
                 json.dumps(item.recommendations, ensure_ascii=False),
                 json.dumps(item.complaints, ensure_ascii=False),
                 item.anamnesis,
                 json.dumps(item.medications, ensure_ascii=False),
                 item.visit_date.isoformat() if item.visit_date else None,
                 item.doctor_name, item.department),
            )
        conn.commit()