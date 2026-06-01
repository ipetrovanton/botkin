"""Pipeline обработки документа: classify → extract → persist."""
import asyncio
import json
import logging
from pathlib import Path

from .concurrency import LLM_SEMAPHORE
from .contracts import LabResult, Prescription, DoctorReport
from .db.connection import get_conn
from .db.repos.document import DocumentRepo

log = logging.getLogger("botkin.orchestrator")

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------
DOC_TYPE_NAMES: dict[str, str] = {
    "analysis": "Анализы 🧪",
    "prescription": "Рецепт 💊",
    "doctor_report": "Заключение врача 👨‍⚕️",
    "certificate": "Справка 📄",
    "unknown": "Документ 📄",
}


# ---------------------------------------------------------------------------
# Вспомогательные
# ---------------------------------------------------------------------------
async def _notify_user(telegram_user_id: int | None, text: str) -> None:
    if not telegram_user_id:
        return
    import os
    from aiogram import Bot
    from aiogram.enums import ParseMode
    from aiogram.client.default import DefaultBotProperties
    token = os.getenv("TG_BOT_TOKEN")
    if not token:
        log.error("TG_BOT_TOKEN is not set, cannot send notification")
        return
    try:
        bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        await bot.send_message(chat_id=telegram_user_id, text=text)
        await bot.session.close()
    except Exception as e:
        log.error("Failed to send Telegram notification to %d: %s", telegram_user_id, e)


def _mark_failed(document_id: int, task_kind: str, error: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE documents SET status = 'failed' WHERE id = ?", (document_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Основной pipeline
# ---------------------------------------------------------------------------
async def process_document(document_id: int, telegram_user_id: int | None = None) -> None:
    try:
        await _process_document_internal(document_id, telegram_user_id)
    except Exception as e:
        log.exception("Global pipeline failure for %d", document_id)
        _mark_failed(document_id, "pipeline_global", str(e))
        await _notify_user(
            telegram_user_id,
            f"❌ <b>Системная ошибка обработки</b> для документа #{document_id}.\n"
            f"Пожалуйста, обратитесь к администратору или попробуйте еще раз.\n"
            f"Ошибка: {e}",
        )


async def _process_document_internal(
    document_id: int, telegram_user_id: int | None = None
) -> None:
    """Полный pipeline: classify → extract → persist."""
    from parsing.llm import classify, extract

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

    # --- 1. Статус: processing ---
    with get_conn() as conn:
        DocumentRepo(conn, user_id).set_status(document_id, "processing")

    # --- 2. Classify (VLM) ---
    async with LLM_SEMAPHORE:
        try:
            classify_result = await asyncio.get_event_loop().run_in_executor(
                None, classify.run_vlm, source_path
            )
        except Exception as e:
            log.exception("Classify failed for %d", document_id)
            _mark_failed(document_id, "classify", str(e))
            await _notify_user(
                telegram_user_id,
                f"❌ <b>Сбой классификации</b> для документа #{document_id}.\nОшибка: {e}",
            )
            return

    doc_type = classify_result.doc_type
    log.info(
        "Doc %d classified as %s (conf=%.2f)",
        document_id, doc_type, classify_result.confidence,
    )

    with get_conn() as conn:
        conn.execute(
            "UPDATE documents SET doc_type = ? WHERE id = ?", (doc_type, document_id)
        )
        conn.commit()

    # --- 3. Extract (VLM) ---
    async with LLM_SEMAPHORE:
        try:
            if doc_type == "analysis":
                items: list[LabResult] = await asyncio.get_event_loop().run_in_executor(
                    None, extract.run_analysis, source_path
                )
                _persist_lab(document_id, user_id, items)

            elif doc_type == "prescription":
                items: list[Prescription] = await asyncio.get_event_loop().run_in_executor(
                    None, extract.run_prescription, source_path
                )
                _persist_prescription(document_id, user_id, items)

            elif doc_type == "doctor_report":
                items: list[DoctorReport] = await asyncio.get_event_loop().run_in_executor(
                    None, extract.run_doctor_report, source_path
                )
                _persist_doctor_report(document_id, user_id, items)

            else:
                log.info("Doc %d type=%s — extract пропускаем", document_id, doc_type)
        except Exception as e:
            log.exception("Extract failed for %d", document_id)
            _mark_failed(document_id, "extract", str(e))
            await _notify_user(
                telegram_user_id,
                f"❌ <b>Сбой извлечения данных</b> для документа #{document_id}.\nОшибка: {e}",
            )
            return

    # --- 4. Финал ---
    with get_conn() as conn:
        DocumentRepo(conn, user_id).set_status(document_id, "extracted")

    log.info("✅ Doc %d processed", document_id)
    pretty_type = DOC_TYPE_NAMES.get(doc_type, "Документ 📄")
    await _notify_user(
        telegram_user_id,
        f"✅ <b>Документ #{document_id}</b> ({pretty_type}) успешно обработан!\n"
        f"Используйте команду /show или /last, чтобы просмотреть извлеченные данные.",
    )


# ---------------------------------------------------------------------------
# Persist-функции
# ---------------------------------------------------------------------------
def _persist_lab(document_id: int, user_id: int, items: list[LabResult]) -> None:
    with get_conn() as conn:
        for item in items:
            conn.execute(
                """
                INSERT INTO lab_results(document_id, user_id, analyte_code, analyte_name,
                                       value_num, value_text, unit, ref_low, ref_high,
                                       taken_at, source_table_cell)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id, user_id, item.analyte_code, item.analyte_name,
                    item.value_num, item.value_text, item.unit,
                    item.ref_low, item.ref_high,
                    item.taken_at.isoformat() if item.taken_at else None,
                    item.source_table_cell,
                ),
            )
        conn.commit()


def _persist_prescription(
    document_id: int, user_id: int, items: list[Prescription]
) -> None:
    with get_conn() as conn:
        for item in items:
            conn.execute(
                """
                INSERT INTO prescriptions(document_id, user_id, drug_mnn, drug_trade,
                                         dose, frequency, duration_days, prescribed_at,
                                         doctor_name, form_107_1u_flag)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id, user_id, item.drug_mnn, item.drug_trade,
                    item.dose, item.frequency, item.duration_days,
                    item.prescribed_at.isoformat() if item.prescribed_at else None,
                    item.doctor_name, item.form_107_1u_flag,
                ),
            )
        conn.commit()


def _persist_doctor_report(
    document_id: int, user_id: int, items: list[DoctorReport]
) -> None:
    with get_conn() as conn:
        for item in items:
            conn.execute(
                """
                INSERT INTO doctor_reports(document_id, user_id, diagnosis,
                                         recommendations_json, complaints_json,
                                         anamnesis, medications_json,
                                         visit_date, doctor_name, department)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id, user_id, item.diagnosis,
                    json.dumps(item.recommendations, ensure_ascii=False),
                    json.dumps(item.complaints, ensure_ascii=False),
                    item.anamnesis,
                    json.dumps(item.medications, ensure_ascii=False),
                    item.visit_date.isoformat() if item.visit_date else None,
                    item.doctor_name, item.department,
                ),
            )
        conn.commit()