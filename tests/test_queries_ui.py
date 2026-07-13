"""Чтение через единый слой репозиториев (DocumentRepo/LabRepo)."""
from datetime import datetime

from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, LabRepo, UserRepo


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
    uid, ids = _seed(1)
    with get_conn() as conn:
        assert DocumentRepo(conn, uid).get(ids[0])["id"] == ids[0]
        assert DocumentRepo(conn, uid + 999).get(ids[0]) is None  # чужой — None


def test_get_document_status(set_test_db):
    uid, ids = _seed(1)
    with get_conn() as conn:
        assert DocumentRepo(conn, uid).get_status(ids[0]) == "received"


def test_adjacent_document_navigation(set_test_db):
    """Сосед по дате через SQL, тай-брейк по id (created_at одинаков в _seed)."""
    uid, ids = _seed(3)          # ids по возрастанию → c новее всех, a старее всех
    a, b, c = ids
    with get_conn() as conn:
        repo = DocumentRepo(conn, uid)
        # старее (prev в ленте по убыванию даты)
        assert repo.adjacent_id(b, older=True) == a
        assert repo.adjacent_id(a, older=True) is None    # самый старый
        # новее (next)
        assert repo.adjacent_id(b, older=False) == c
        assert repo.adjacent_id(c, older=False) is None   # самый новый


def test_adjacent_document_owner_scoped(set_test_db):
    uid, ids = _seed(2)
    with get_conn() as conn:
        assert DocumentRepo(conn, uid + 999).adjacent_id(ids[0], older=True) is None   # чужой


def test_count_and_list_documents_with_filter_and_paging(set_test_db):
    uid, _ = _seed(3, "analysis")
    with get_conn() as conn:
        DocumentRepo(conn, uid).create(source_path="/tmp/p.jpg", doc_type="doctor_report")
        repo = DocumentRepo(conn, uid)
        assert repo.count() == 4
        assert repo.count(doc_type="analysis") == 3
        page = repo.list(doc_type="analysis", limit=2, offset=0)
    assert len(page) == 2
    assert all(d["doc_type"] == "analysis" for d in page)


def test_documents_in_period(set_test_db):
    uid, ids = _seed(2)
    with get_conn() as conn:
        conn.execute("UPDATE documents SET created_at='2026-05-10 10:00:00' WHERE id=?", (ids[0],))
        conn.execute("UPDATE documents SET created_at='2026-04-01 10:00:00' WHERE id=?", (ids[1],))
        res = DocumentRepo(conn, uid).in_period(
            datetime(2026, 5, 1), datetime(2026, 5, 31, 23, 59, 59))
    assert [d["id"] for d in res] == [ids[0]]


def test_labs_in_period_grouped(set_test_db):
    uid, ids = _seed(1)
    did = ids[0]
    with get_conn() as conn:
        for name, val, taken in [("Глюкоза", 5.4, "2026-05-02"), ("Глюкоза", 4.9, "2026-05-20"),
                                  ("Гемоглобин", 145, "2026-05-10")]:
            conn.execute(
                "INSERT INTO lab_results(document_id, user_id, analyte_name, value_num, taken_at) "
                "VALUES (?,?,?,?,?)", (did, uid, name, val, taken))
        groups = LabRepo(conn, uid).in_period(datetime(2026, 5, 1), datetime(2026, 5, 31))
    by_name = {g["analyte_name"]: g["points"] for g in groups}
    assert [p["value_num"] for p in by_name["Глюкоза"]] == [5.4, 4.9]  # по времени
    assert len(by_name["Гемоглобин"]) == 1


def test_for_document_returns_extended_fields(set_test_db):
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(7001)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/a.jpg")
        conn.execute(
            "INSERT INTO lab_results(document_id, user_id, analyte_name, value_text, "
            "ref_operator, ref_high, ref_text, analyte_canonical, loinc, match_status, "
            "unit_expected, unit_mismatch) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (did, uid, "СРБ", None, "<", 5.0, None, "С-реактивный белок",
             "1988-5", "matched", "мг/л", 0),
        )
        rows = LabRepo(conn, uid).for_document(did)
    r = rows[0]
    assert r["value_text"] is None and r["ref_operator"] == "<"
    assert r["analyte_canonical"] == "С-реактивный белок"
    assert r["loinc"] == "1988-5" and r["match_status"] == "matched"
    assert r["unit_expected"] == "мг/л" and r["unit_mismatch"] == 0


def test_for_document_returns_all_rows_in_insertion_order(set_test_db):
    # Регресс: панель из 21 показателя (ОАК+СРБ) обрезалась дефолтным LIMIT 20, и
    # без ORDER BY терялась последняя вставленная строка (СОЭ). Карточка обязана
    # показывать ВСЕ строки документа в порядке документа.
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(7002)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/b.pdf")
        for i in range(21):
            conn.execute(
                "INSERT INTO lab_results(document_id, user_id, analyte_name, value_num) "
                "VALUES (?,?,?,?)",
                (did, uid, f"Показатель {i:02d}", float(i)),
            )
        rows = LabRepo(conn, uid).for_document(did)
    assert len(rows) == 21
    # Порядок вставки сохранён: первая и последняя строки на своих местах.
    assert rows[0]["analyte_name"] == "Показатель 00"
    assert rows[-1]["analyte_name"] == "Показатель 20"


def test_for_document_scoped_to_owner(set_test_db):
    # Тенант-изоляция: чужой user_id не видит строки документа.
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(7003)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/c.pdf")
        conn.execute(
            "INSERT INTO lab_results(document_id, user_id, analyte_name, value_num) "
            "VALUES (?,?,?,?)", (did, uid, "Глюкоза", 5.1))
        mine = LabRepo(conn, uid).for_document(did)
        other = LabRepo(conn, uid + 999).for_document(did)
    assert len(mine) == 1
    assert other == []
