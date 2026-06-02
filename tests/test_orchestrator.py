import asyncio
from unittest.mock import patch

from botkin.domain.models import ClassifyResult, Prescription


def _make_doc(source_path="/tmp/x.jpg"):
    from botkin.db.connection import get_conn
    from botkin.db.repos import DocumentRepo, UserRepo
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(777)
        did = DocumentRepo(conn, uid).create(source_path=source_path)
    return uid, did


async def _anoop(*args, **kwargs):
    return None


def test_prescription_drug_normalized_and_raw_saved(set_test_db, monkeypatch):
    from botkin.pipeline import orchestrator
    from botkin.db.connection import get_conn
    monkeypatch.setattr(orchestrator, "DELIVERY_FALLBACK_DELAY", 0.0)

    uid, did = _make_doc()

    with patch.object(orchestrator.classify, "run_vlm",
                      return_value=ClassifyResult(doc_type="prescription", confidence=0.9)), \
         patch.object(orchestrator.extract, "run_prescription",
                      return_value=[Prescription(drug_mnn="элкап", drug_trade="элкап")]), \
         patch("botkin.pipeline.orchestrator.notify_user", side_effect=_anoop):
        asyncio.run(orchestrator.process_document(did, 777))

    with get_conn() as conn:
        row = conn.execute(
            "SELECT drug_mnn, drug_trade, drug_raw, match_status, reg_statuses "
            "FROM prescriptions WHERE document_id=?", (did,)).fetchone()
        doc = conn.execute("SELECT status, raw_extraction FROM documents WHERE id=?", (did,)).fetchone()

    assert row["drug_raw"] == "элкап"           # оригинал сохранён
    assert row["drug_trade"] == "Элькар"        # торговое нормализовано по справочнику
    assert row["drug_mnn"] == "Левокарнитин"    # МНН дозаполнен из связки реестра
    assert row["match_status"] == "matched"
    assert "modified" in row["reg_statuses"]    # статус-список из ГРЛС сохранён
    assert doc["status"] == "extracted"
    assert doc["raw_extraction"] and "элкап" in doc["raw_extraction"]   # сырой JSON сохранён
