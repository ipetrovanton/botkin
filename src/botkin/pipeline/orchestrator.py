"""Pipeline обработки документа: classify → extract → normalize → persist."""
import asyncio
import json
import logging
from pathlib import Path

from botkin.config import (
    DELIVERY_FALLBACK_DELAY, IMAGE_CLASSIFY_LONG_SIDE, IMAGE_EXTRACT_LONG_SIDE,
    PDF_RENDER_DPI, VLM_MODEL, VLM_NUM_CTX, VLM_NUM_PREDICT, VLM_TEMPERATURE,
)
from botkin.db.connection import get_conn, transaction
from botkin.db.repos import DocumentRepo
from botkin.domain.models import ClassifyResult, DoctorReport, LabResult
from botkin.exceptions import ClassificationError, ExtractionError
from botkin.llm import classify, extract
from botkin.normalize.drugs import DrugNormalizer, load_default
from botkin.normalize.analytes import (
    HEMATOLOGY_GROUP,
    AnalyteNormalizer,
    is_cbc_analyte,
    is_cbc_panel,
    summary_title,
)
from botkin.normalize.analytes import load_default as load_analytes
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
    except Exception:
        log.exception("Global pipeline failure for %d", document_id)
        _mark_failed(document_id)
        await notify_user(telegram_user_id, pipeline_failed(document_id))


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

    # 1. Статус: распознавание
    with get_conn() as conn:
        DocumentRepo(conn, user_id).set_status(document_id, "recognizing")

    # 2. Classify (VLM)
    async with LLM_SEMAPHORE:
        try:
            result = await asyncio.to_thread(classify.run_vlm, source_path)
        except ClassificationError as e:
            log.error("Doc %d: сбой классификации: %s", document_id, e)
            _mark_failed(document_id)
            await notify_user(telegram_user_id, classify_failed(document_id))
            return

    doc_type = result.doc_type
    log.info("Doc %d classified as %s (conf=%.2f)", document_id, doc_type, result.confidence)

    with get_conn() as conn:
        repo = DocumentRepo(conn, user_id)
        repo.set_doc_type(document_id, doc_type)
        repo.set_metadata(document_id, result.title, result.clinic)

    # Статус: нормализация (извлечение деталей + нормализация)
    with get_conn() as conn:
        DocumentRepo(conn, user_id).set_status(document_id, "normalizing")

    # 3. Extract (VLM)
    async with LLM_SEMAPHORE:
        try:
            handler = _EXTRACTORS.get(doc_type)
            if handler is not None:
                await handler(document_id, user_id, source_path, result)
            else:
                log.info("Doc %d type=%s — extract пропускаем", document_id, doc_type)
        except ExtractionError as e:
            log.error("Doc %d: сбой извлечения: %s", document_id, e)
            _mark_failed(document_id)
            await notify_user(telegram_user_id, extract_failed(document_id))
            return

    # 4. Финал
    with get_conn() as conn:
        DocumentRepo(conn, user_id).set_status(document_id, "extracted")
    log.info("Doc %d processed", document_id)

    # Push-fallback: ждём, пока поллинг бота покажет результат и захватит доставку.
    await asyncio.sleep(DELIVERY_FALLBACK_DELAY)
    with get_conn() as conn:
        claimed = DocumentRepo(conn, user_id).claim_delivery(document_id)
    if claimed:
        await notify_user(telegram_user_id, document_processed(document_id, doc_type))


# Обработчики по типу документа.
# Добавить тип = добавить async-обработчик и строку в _EXTRACTORS, не трогая _run.
# Незнакомый тип extract пропускает (файл уже сохранён).

async def _extract_analysis(
    document_id: int, user_id: int, source_path: Path, result: ClassifyResult,
) -> None:
    items: list[LabResult] = await asyncio.to_thread(extract.run_analysis, source_path)
    log.info("Doc %d: извлечено строк анализов=%d", document_id, len(items))
    _save_raw_extraction(document_id, items)
    matches = _persist_lab(document_id, user_id, items)
    if not matches:
        return
    # Метрика качества нормализации по ФСЛИ — для сравнения конфигов.
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


async def _extract_doctor_report(
    document_id: int, user_id: int, source_path: Path, result: ClassifyResult,
) -> None:
    items: list[DoctorReport] = await asyncio.to_thread(extract.run_doctor_report, source_path)
    _save_raw_extraction(document_id, items)
    _persist_doctor_report(document_id, user_id, items)


_EXTRACTORS = {
    "analysis": _extract_analysis,
    "doctor_report": _extract_doctor_report,
}


# Хелперы

def _mark_failed(document_id: int) -> None:
    # Без user_id: вызывается в т.ч. из глобального обработчика, который ловит сбой ещё
    # до того, как из БД прочитан владелец документа. Пометка статуса по id безопасна —
    # данные не читаются, только переключается статус собственного документа.
    with get_conn() as conn:
        conn.execute("UPDATE documents SET status = 'failed' WHERE id = ?", (document_id,))
        conn.commit()


def _save_raw_extraction(document_id: int, items: list) -> None:
    """Сохраняет полный сырой ответ модели (до нормализации) — гарантия восстановимости."""
    payload = json.dumps([i.model_dump(mode="json") for i in items], ensure_ascii=False)
    with get_conn() as conn:
        conn.execute("UPDATE documents SET raw_extraction = ? WHERE id = ?", (payload, document_id))
        conn.commit()


# Persist

def _persist_lab(document_id: int, user_id: int, items: list[LabResult]) -> list:
    """Нормализует и сохраняет показатели; возвращает список AnalyteMatch (для заголовка)."""
    normalizer = get_analyte_normalizer()
    matches = []
    # Клиническую группу для ОАК задаёт состав панели, а не ФСЛИ-группа отдельного
    # показателя (Гемоглобин/Эритроциты числятся «Химико-микроскопическими»). Опознаём
    # панель один раз по всему документу и проставляем строкам гематологию.
    is_cbc = is_cbc_panel([item.analyte_name for item in items])
    with get_conn() as conn, transaction(conn):
        for item in items:
            unit_canon, unit_raw = canonical_unit(item.unit)
            match = normalizer.correct(item.analyte_name)
            matches.append(match)
            group = HEMATOLOGY_GROUP if (is_cbc and is_cbc_analyte(item.analyte_name)) else match.group
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
                   VALUES (:document_id, :user_id, :analyte_code, :analyte_name,
                   :value_num, :value_text, :unit, :ref_low, :ref_high, :ref_operator, :ref_text,
                   :taken_at, :source_table_cell, :value_raw, :unit_raw, :taken_at_raw,
                   :analyte_canonical, :loinc, :nmu_code, :analyte_group, :match_status,
                   :unit_expected, :unit_mismatch)""",
                {
                    "document_id": document_id, "user_id": user_id,
                    "analyte_code": item.analyte_code, "analyte_name": item.analyte_name,
                    "value_num": item.value_num, "value_text": item.value_text, "unit": unit_canon,
                    "ref_low": item.ref_low, "ref_high": item.ref_high,
                    "ref_operator": item.ref_operator, "ref_text": item.ref_text,
                    "taken_at": item.taken_at.isoformat() if item.taken_at else None,
                    "source_table_cell": item.source_table_cell,
                    "value_raw": item.value_raw, "unit_raw": unit_raw,
                    "taken_at_raw": item.taken_at_raw,
                    "analyte_canonical": match.canonical, "loinc": match.loinc,
                    "nmu_code": match.nmu, "analyte_group": group,
                    "match_status": match.status, "unit_expected": unit_expected,
                    "unit_mismatch": unit_mismatch,
                },
            )
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
    with get_conn() as conn, transaction(conn):
        for item in items:
            conn.execute(
                """INSERT INTO doctor_reports(document_id, user_id, diagnosis,
                   recommendations_json, complaints_json, anamnesis, medications_json,
                   medications_normalized_json,
                   visit_date, doctor_name, department)
                   VALUES (:document_id, :user_id, :diagnosis,
                   :recommendations_json, :complaints_json, :anamnesis, :medications_json,
                   :medications_normalized_json,
                   :visit_date, :doctor_name, :department)""",
                {
                    "document_id": document_id, "user_id": user_id, "diagnosis": item.diagnosis,
                    "recommendations_json": json.dumps(item.recommendations, ensure_ascii=False),
                    "complaints_json": json.dumps(item.complaints, ensure_ascii=False),
                    "anamnesis": item.anamnesis,
                    "medications_json": json.dumps(item.medications, ensure_ascii=False),
                    "medications_normalized_json": _normalize_medications(item.medications),
                    "visit_date": item.visit_date.isoformat() if item.visit_date else None,
                    "doctor_name": item.doctor_name, "department": item.department,
                },
            )