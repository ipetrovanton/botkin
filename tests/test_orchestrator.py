import asyncio
from unittest.mock import patch

from botkin.domain.models import ClassifyResult


def _make_doc(source_path="/tmp/x.jpg"):
    from botkin.db.connection import get_conn
    from botkin.db.repos import DocumentRepo, UserRepo
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(777)
        did = DocumentRepo(conn, uid).create(source_path=source_path)
    return uid, did


async def _anoop(*args, **kwargs):
    return None


def test_unknown_doc_saved_without_extraction(set_test_db, monkeypatch):
    """Неподдерживаемый тип (например, рецепт → unknown) сохраняется без извлечения деталей."""
    from botkin.pipeline import orchestrator
    from botkin.db.connection import get_conn
    monkeypatch.setattr(orchestrator, "DELIVERY_FALLBACK_DELAY", 0.0)

    uid, did = _make_doc()

    with patch.object(orchestrator.classify, "run_vlm",
                      return_value=ClassifyResult(doc_type="unknown", confidence=0.9)), \
         patch("botkin.pipeline.orchestrator.notify_user", side_effect=_anoop):
        asyncio.run(orchestrator.process_document(did, 777))

    with get_conn() as conn:
        doc = conn.execute(
            "SELECT doc_type, status, raw_extraction FROM documents WHERE id=?", (did,)).fetchone()
        labs = conn.execute("SELECT COUNT(*) c FROM lab_results WHERE document_id=?", (did,)).fetchone()
        reports = conn.execute("SELECT COUNT(*) c FROM doctor_reports WHERE document_id=?", (did,)).fetchone()

    assert doc["doc_type"] == "unknown"
    assert doc["status"] == "extracted"          # документ обработан и доставлен
    assert doc["raw_extraction"] is None          # деталей не извлекали
    assert labs["c"] == 0 and reports["c"] == 0   # никаких записей деталей


def test_persist_lab_normalizes_and_checks_unit(set_test_db, monkeypatch):
    from botkin.db.connection import get_conn
    from botkin.db.repos import DocumentRepo, UserRepo
    from botkin.domain.models import LabResult
    from botkin.normalize.analytes import AnalyteNormalizer
    from botkin.pipeline import orchestrator

    # Детерминированный нормализатор (не зависим от содержимого реального реестра).
    fake = AnalyteNormalizer([
        {"name": "Глюкоза", "synonyms": ["GLU", "Glucose"], "units": ["ммоль/л"],
         "group": "Биохимические исследования"},
    ])
    monkeypatch.setattr(orchestrator, "_ANALYTE_NORMALIZER", fake)

    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(9100)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/a.jpg")

    items = [
        LabResult(analyte_name="Глюкоэа", value_num=5.4, unit="г/л"),  # опечатка + неверная единица
    ]
    matches = orchestrator._persist_lab(did, uid, items)

    with get_conn() as conn:
        row = conn.execute(
            "SELECT analyte_name, analyte_canonical, match_status, loinc, "
            "unit_expected, unit_mismatch FROM lab_results WHERE document_id=?",
            (did,),
        ).fetchone()
    assert row["analyte_name"] == "Глюкоэа"          # исходное имя не перезаписано
    assert row["analyte_canonical"] == "Глюкоза"      # нормализовано
    assert row["match_status"] == "matched"
    assert row["unit_expected"] == "ммоль/л"
    assert row["unit_mismatch"] == 1                  # г/л ≠ ммоль/л

    # _persist_lab возвращает matches с группой → обобщённый заголовок документа по группе
    from botkin.normalize.analytes import summary_title
    assert matches[0].canonical == "Глюкоза"
    assert summary_title([m.group for m in matches]) == "Биохимические исследования"


def test_persist_lab_overrides_cbc_group(set_test_db, monkeypatch):
    """Документ ОАК: мусорная ФСЛИ-группа показателей перезаписывается на гематологию."""
    from botkin.db.connection import get_conn
    from botkin.db.repos import DocumentRepo, UserRepo
    from botkin.domain.models import LabResult
    from botkin.normalize.analytes import AnalyteNormalizer
    from botkin.pipeline import orchestrator

    # В реестре ФСЛИ Гемоглобин/Эритроциты/Лейкоциты помечены «Химико-микроскопическими».
    fake = AnalyteNormalizer([
        {"name": "Гемоглобин", "units": ["г/л"], "group": "Химико-микроскопические исследования"},
        {"name": "Эритроциты", "units": ["10^12/л"], "group": "Химико-микроскопические исследования"},
        {"name": "Лейкоциты", "units": ["10^9/л"], "group": "Химико-микроскопические исследования"},
        {"name": "Тромбоциты", "units": ["10^9/л"], "group": "Гематологические исследования"},
    ])
    monkeypatch.setattr(orchestrator, "_ANALYTE_NORMALIZER", fake)

    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(9200)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/cbc.jpg")

    items = [
        LabResult(analyte_name="Гемоглобин", value_num=137, unit="г/л"),
        LabResult(analyte_name="Эритроциты", value_num=4.6, unit="10^12/л"),
        LabResult(analyte_name="Лейкоциты", value_num=5.1, unit="10^9/л"),
        LabResult(analyte_name="Тромбоциты", value_num=217, unit="10^9/л"),
    ]
    orchestrator._persist_lab(did, uid, items)

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT analyte_group FROM lab_results WHERE document_id=? ORDER BY id", (did,),
        ).fetchall()
    # Состав опознан как ОАК → все строки в гематологии, мусорная химмикро-группа исправлена.
    assert [r["analyte_group"] for r in rows] == ["Гематологические исследования"] * 4
