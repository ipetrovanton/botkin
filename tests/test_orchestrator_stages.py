import asyncio
from unittest.mock import patch

from botkin.domain.models import ClassifyResult, LabResult


async def _anoop(*a, **k):
    return None


def _make_doc():
    from botkin.db.connection import get_conn
    from botkin.db.repos import DocumentRepo, UserRepo
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(321)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/a.jpg")
    return uid, did


def test_title_clinic_saved_after_classify(set_test_db, monkeypatch):
    from botkin.pipeline import orchestrator
    from botkin.db.connection import get_conn
    monkeypatch.setattr(orchestrator, "DELIVERY_FALLBACK_DELAY", 0.0)
    uid, did = _make_doc()
    with patch.object(orchestrator.classify, "run_vlm",
                      return_value=ClassifyResult(doc_type="analysis", confidence=0.9,
                                                  title="Биохимия", clinic="Инвитро")), \
         patch.object(orchestrator.extract, "run_analysis",
                      return_value=[LabResult(analyte_name="Глюкоза", value_num=5.0)]), \
         patch("botkin.pipeline.orchestrator.notify_user", side_effect=_anoop):
        asyncio.run(orchestrator.process_document(did, 321))
    with get_conn() as conn:
        row = conn.execute("SELECT title, clinic, status FROM documents WHERE id=?", (did,)).fetchone()
    assert row["title"] == "Биохимия"
    assert row["clinic"] == "Инвитро"
    assert row["status"] == "extracted"


def test_stages_recorded(set_test_db, monkeypatch):
    """Стадии recognizing и normalizing проставляются по ходу."""
    from botkin.pipeline import orchestrator
    from botkin.db.connection import get_conn
    monkeypatch.setattr(orchestrator, "DELIVERY_FALLBACK_DELAY", 0.0)
    uid, did = _make_doc()
    seen = []

    def _spy_run_analysis(_path):
        with get_conn() as conn:
            seen.append(conn.execute("SELECT status FROM documents WHERE id=?", (did,)).fetchone()["status"])
        return [LabResult(analyte_name="Глюкоза", value_num=5.0)]

    with patch.object(orchestrator.classify, "run_vlm",
                      return_value=ClassifyResult(doc_type="analysis", confidence=0.9)), \
         patch.object(orchestrator.extract, "run_analysis", side_effect=_spy_run_analysis), \
         patch("botkin.pipeline.orchestrator.notify_user", side_effect=_anoop):
        asyncio.run(orchestrator.process_document(did, 321))
    # к моменту извлечения деталей статус уже normalizing (ставится перед extract)
    assert "normalizing" in seen
