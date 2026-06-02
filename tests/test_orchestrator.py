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
