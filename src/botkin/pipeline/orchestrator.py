"""Pipeline обработки документа: classify → extract → normalize → persist."""
import asyncio
import json
import logging
from pathlib import Path

from botkin.config import (
    DELIVERY_FALLBACK_DELAY, IMAGE_CLASSIFY_LONG_SIDE, IMAGE_EXTRACT_LONG_SIDE,
    PDF_RENDER_DPI, VLM_MODEL, VLM_NUM_CTX, VLM_NUM_PREDICT, VLM_TEMPERATURE,
)
from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo
from botkin.domain.models import LabResult, DoctorReport
from botkin.exceptions import ClassificationError, ExtractionError
from botkin.llm import classify, extract
from botkin.normalize.drugs import DrugNormalizer, load_default
from botkin.normalize.analytes import AnalyteNormalizer, load_default as load_analytes, summary_title
from botkin.normalize.units import canonical_unit
from botkin.pipeline.notifications import (
    classify_failed, document_processed, extract_failed, notify_user, pipeline_failed,
)

log = logging.getLogger("botkin.pipeline")

LLM_SEMAPHORE = asyncio.Semaphore(1)

_DRUG_NORMALIZER: DrugNormalizer | None = None


def get_drug_normalizer() -> DrugNormalizer:
    """Ленивый синглтон: справочник лекарств читается из registry.jsonl один раз."""
    global _DRUG_NORMALIZER
    if _DRUG_NORMALIZER is None:
        _DRUG_NORMALIZER = load_default()
    return _DRUG_NORMALIZER


_ANALYTE_NORMALIZER: AnalyteNormalizer | None = None


def get_analyte_normalizer() -> AnalyteNormalizer:
    """Ленивый синглтон: справочник анализов ФСЛИ читается из registry.jsonl один раз."""
    global _ANALYTE_NORMALIZER
    if _ANALYTE_NORMALIZER is None:
        _ANALYTE_NORMALIZER = load_analytes()
    return _ANALYTE_NORMALIZER


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

    log.info(
        "[CONFIG] Doc %d | model=%s temp=%.2f num_ctx=%d num_predict=%d | "
        "extract_long_side=%d classify_long_side=%d pdf_dpi=%d",
        document_id, VLM_MODEL, VLM_TEMPERATURE, VLM_NUM_CTX, VLM_NUM_PREDICT,
        IMAGE_EXTRACT_LONG_SIDE, IMAGE_CLASSIFY_LONG_SIDE, PDF_RENDER_DPI,
    )

    # ── 1. Статус: распознавание ───────────────────────────────────────────
    with get_conn() as conn:
        DocumentRepo(conn, user_id).set_status(document_id, "recognizing")

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
        repo = DocumentRepo(conn, user_id)
        repo.set_doc_type(document_id, doc_type)
        repo.set_metadata(document_id, result.title, result.clinic)

    # ── Статус: нормализация (извлечение деталей + нормализация) ────────────
    with get_conn() as conn:
        DocumentRepo(conn, user_id).set_status(document_id, "normalizing")

    # ── 3. Extract (VLM) ───────────────────────────────────────────────────
    async with LLM_SEMAPHORE:
        try:
            if doc_type == "analysis":
                items: list[LabResult] = await asyncio.get_event_loop().run_in_executor(
                    None, extract.run_analysis, source_path,
                )
                log.info("Doc %d: извлечено строк анализов=%d", document_id, len(items))
                _save_raw_extraction(document_id, items)
                matches = _persist_lab(document_id, user_id, items)
                # Метрика качества нормализации по ФСЛИ — для сравнения конфигов.
                if matches:
                    matched = sum(1 for m in matches if m.status == "matched")
                    log.info(
                        "[NORMALIZE_QUALITY] Doc %d | сопоставлено ФСЛИ: %d/%d | не распознано: %d",
                        document_id, matched, len(matches), len(matches) - matched,
                    )
                    # Обобщённый заголовок по группе исследований (биоматериал не используем).
                    title = summary_title(
                        [m.group for m in matches],
                        test_names=[m.canonical or m.raw for m in matches],
                    )
                    with get_conn() as conn:
                        DocumentRepo(conn, user_id).set_metadata(document_id, title, result.clinic)
                    log.info("Doc %d: заголовок обобщён → '%s'", document_id, title)

            elif doc_type == "doctor_report":
                items: list[DoctorReport] = await asyncio.get_event_loop().run_in_executor(
                    None, extract.run_doctor_report, source_path,
                )
                _save_raw_extraction(document_id, items)
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

    # Push-fallback: ждём, пока поллинг бота покажет результат и захватит доставку.
    await asyncio.sleep(DELIVERY_FALLBACK_DELAY)
    with get_conn() as conn:
        claimed = DocumentRepo(conn, user_id).claim_delivery(document_id)
    if claimed:
        await notify_user(telegram_user_id, document_processed(document_id, doc_type))


# ── Хелперы ────────────────────────────────────────────────────────────────────

def _mark_failed(document_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE documents SET status = 'failed' WHERE id = ?", (document_id,))
        conn.commit()


def _save_raw_extraction(document_id: int, items: list) -> None:
    """Сохраняет полный сырой ответ модели (до нормализации) — гарантия восстановимости."""
    payload = json.dumps([i.model_dump(mode="json") for i in items], ensure_ascii=False)
    with get_conn() as conn:
        conn.execute("UPDATE documents SET raw_extraction = ? WHERE id = ?", (payload, document_id))
        conn.commit()


# ── Persist ────────────────────────────────────────────────────────────────────

def _persist_lab(document_id: int, user_id: int, items: list[LabResult]) -> list:
    """Нормализует и сохраняет показатели; возвращает список AnalyteMatch (для заголовка)."""
    normalizer = get_analyte_normalizer()
    matches = []
    with get_conn() as conn:
        for item in items:
            unit_canon, unit_raw = canonical_unit(item.unit)
            match = normalizer.correct(item.analyte_name)
            matches.append(match)
            # Единица из документа сверяется с НАБОРОМ известных единиц показателя:
            # совпадение хотя бы с одной канонической формой → ок (нет ложных ⚠️).
            unit_mismatch = None
            unit_expected = match.expected_units[0] if match.expected_units else None
            if match.status == "matched" and match.expected_units and unit_canon:
                known = {canonical_unit(u)[0] for u in match.expected_units}
                unit_mismatch = 0 if unit_canon in known else 1
            conn.execute(
                """INSERT INTO lab_results(document_id, user_id, analyte_code, analyte_name,
                   value_num, value_text, unit, ref_low, ref_high, ref_operator, ref_text,
                   taken_at, source_table_cell, value_raw, unit_raw, taken_at_raw,
                   analyte_canonical, loinc, nmu_code, analyte_group, match_status,
                   unit_expected, unit_mismatch)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (document_id, user_id, item.analyte_code, item.analyte_name,
                 item.value_num, item.value_text, unit_canon,
                 item.ref_low, item.ref_high, item.ref_operator, item.ref_text,
                 item.taken_at.isoformat() if item.taken_at else None,
                 item.source_table_cell,
                 item.value_raw, unit_raw, item.taken_at_raw,
                 match.canonical, match.loinc, match.nmu, match.group,
                 match.status, unit_expected, unit_mismatch),
            )
        conn.commit()
    return matches


def _normalize_medications(lines: list[str]) -> str:
    """Best-effort нормализация строк medications (свободный текст с дозой)."""
    normalizer = get_drug_normalizer()
    out = []
    for line in lines:
        m = normalizer.correct_free_text(line)
        out.append({"raw": m.raw, "canonical": m.canonical, "mnn": m.mnn,
                    "statuses": list(m.statuses), "status": m.status})
    return json.dumps(out, ensure_ascii=False)


def _persist_doctor_report(document_id: int, user_id: int, items: list[DoctorReport]) -> None:
    with get_conn() as conn:
        for item in items:
            conn.execute(
                """INSERT INTO doctor_reports(document_id, user_id, diagnosis,
                   recommendations_json, complaints_json, anamnesis, medications_json,
                   medications_normalized_json,
                   visit_date, doctor_name, department)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (document_id, user_id, item.diagnosis,
                 json.dumps(item.recommendations, ensure_ascii=False),
                 json.dumps(item.complaints, ensure_ascii=False),
                 item.anamnesis,
                 json.dumps(item.medications, ensure_ascii=False),
                 _normalize_medications(item.medications),
                 item.visit_date.isoformat() if item.visit_date else None,
                 item.doctor_name, item.department),
            )
        conn.commit()