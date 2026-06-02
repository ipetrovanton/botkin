from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, UserRepo


def test_set_metadata(set_test_db):
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(7)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/x.jpg")
        DocumentRepo(conn, uid).set_metadata(did, title="ОАК", clinic="Гемотест")
        row = conn.execute("SELECT title, clinic FROM documents WHERE id=?", (did,)).fetchone()
    assert row["title"] == "ОАК"
    assert row["clinic"] == "Гемотест"


def test_claim_delivery_atomic(set_test_db):
    """Первый захват возвращает True, повторный — False."""
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(8)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/y.jpg")
        assert DocumentRepo(conn, uid).claim_delivery(did) is True
        assert DocumentRepo(conn, uid).claim_delivery(did) is False
