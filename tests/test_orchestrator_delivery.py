import asyncio
from unittest.mock import patch

from botkin.domain.models import ClassifyResult, LabResult


def _make_doc():
    from botkin.db.connection import get_conn
    from botkin.db.repos import DocumentRepo, UserRepo
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(999)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/a.jpg")
    return uid, did


def test_fallback_sends_when_bot_did_not_deliver(set_test_db, monkeypatch):
    from botkin.pipeline import orchestrator
    monkeypatch.setattr(orchestrator, "DELIVERY_FALLBACK_DELAY", 0.0)
    uid, did = _make_doc()
    sent = []

    async def spy_notify(tg_id, text):
        sent.append(text)

    with patch.object(orchestrator.classify, "run_vlm",
                      return_value=ClassifyResult(doc_type="analysis", confidence=0.9)), \
         patch.object(orchestrator.extract, "run_analysis",
                      return_value=[LabResult(analyte_name="Глюкоза", value_num=5.0)]), \
         patch("botkin.pipeline.orchestrator.notify_user", side_effect=spy_notify):
        asyncio.run(orchestrator.process_document(did, 999))
    assert len(sent) == 1   # бот не доставил → fallback отправил


def test_fallback_silent_when_bot_delivered(set_test_db, monkeypatch):
    from botkin.pipeline import orchestrator
    from botkin.db.connection import get_conn
    from botkin.db.repos import DocumentRepo
    monkeypatch.setattr(orchestrator, "DELIVERY_FALLBACK_DELAY", 0.0)
    uid, did = _make_doc()
    sent = []

    async def spy_notify(tg_id, text):
        sent.append(text)

    def deliver_during_extract(_path):
        with get_conn() as conn:
            DocumentRepo(conn, uid).claim_delivery(did)  # имитируем доставку ботом
        return [LabResult(analyte_name="Глюкоза", value_num=5.0)]

    with patch.object(orchestrator.classify, "run_vlm",
                      return_value=ClassifyResult(doc_type="analysis", confidence=0.9)), \
         patch.object(orchestrator.extract, "run_analysis", side_effect=deliver_during_extract), \
         patch("botkin.pipeline.orchestrator.notify_user", side_effect=spy_notify):
        asyncio.run(orchestrator.process_document(did, 999))
    assert sent == []   # бот уже доставил → fallback молчит
