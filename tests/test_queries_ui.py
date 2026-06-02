from datetime import datetime

from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, UserRepo


def _seed(n=3, doc_type="analysis"):
    """Создаёт пользователя и n документов, возвращает (uid, [doc_id...])."""
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(42)
        ids = []
        for i in range(n):
            did = DocumentRepo(conn, uid).create(source_path=f"/tmp/{i}.jpg", doc_type=doc_type)
            ids.append(did)
    return uid, ids


def test_get_document_checks_owner(set_test_db):
    from botkin.db.queries import get_document
    uid, ids = _seed(1)
    assert get_document(ids[0], uid)["id"] == ids[0]
    assert get_document(ids[0], uid + 999) is None  # чужой — None


def test_get_document_status(set_test_db):
    from botkin.db.queries import get_document_status
    uid, ids = _seed(1)
    assert get_document_status(ids[0], uid) == "received"


def test_count_and_list_documents_with_filter_and_paging(set_test_db):
    from botkin.db.queries import count_documents, list_documents
    uid, _ = _seed(3, "analysis")
    with get_conn() as conn:
        DocumentRepo(conn, uid).create(source_path="/tmp/p.jpg", doc_type="doctor_report")
    assert count_documents(uid) == 4
    assert count_documents(uid, doc_type="analysis") == 3
    page = list_documents(uid, doc_type="analysis", limit=2, offset=0)
    assert len(page) == 2
    assert all(d["doc_type"] == "analysis" for d in page)


def test_documents_in_period(set_test_db):
    from botkin.db.queries import documents_in_period
    uid, ids = _seed(2)
    with get_conn() as conn:
        conn.execute("UPDATE documents SET created_at='2026-05-10 10:00:00' WHERE id=?", (ids[0],))
        conn.execute("UPDATE documents SET created_at='2026-04-01 10:00:00' WHERE id=?", (ids[1],))
    res = documents_in_period(uid, datetime(2026, 5, 1), datetime(2026, 5, 31, 23, 59, 59))
    assert [d["id"] for d in res] == [ids[0]]


def test_labs_in_period_grouped(set_test_db):
    from botkin.db.queries import labs_in_period
    uid, ids = _seed(1)
    did = ids[0]
    with get_conn() as conn:
        for name, val, taken in [("Глюкоза", 5.4, "2026-05-02"), ("Глюкоза", 4.9, "2026-05-20"),
                                  ("Гемоглобин", 145, "2026-05-10")]:
            conn.execute(
                "INSERT INTO lab_results(document_id, user_id, analyte_name, value_num, taken_at) "
                "VALUES (?,?,?,?,?)", (did, uid, name, val, taken))
    groups = labs_in_period(uid, datetime(2026, 5, 1), datetime(2026, 5, 31))
    by_name = {g["analyte_name"]: g["points"] for g in groups}
    assert [p["value_num"] for p in by_name["Глюкоза"]] == [5.4, 4.9]  # по времени
    assert len(by_name["Гемоглобин"]) == 1
