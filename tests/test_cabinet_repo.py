"""Тесты новых методов репозиториев для веб-кабинета: фильтр, селекторы, статистика."""
from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, LabRepo, ReportRepo, UserRepo


def _seed_user_docs(conn):
    """Готовит пользователя с документами разного типа/клиники/врача."""
    uid = UserRepo(conn).get_or_create(42)
    repo = DocumentRepo(conn, uid)
    # Два анализа (Инвитро) и одно заключение (Садко) у конкретного врача.
    d1 = repo.create(source_path="/tmp/a1.pdf")
    repo.set_doc_type(d1, "analysis")
    repo.set_metadata(d1, title="Общий анализ крови", clinic="Инвитро")
    repo.set_status(d1, "extracted")
    d2 = repo.create(source_path="/tmp/a2.pdf")
    repo.set_doc_type(d2, "analysis")
    repo.set_metadata(d2, title="Биохимия", clinic="Инвитро")
    repo.set_status(d2, "extracted")
    d3 = repo.create(source_path="/tmp/r1.jpg")
    repo.set_doc_type(d3, "doctor_report")
    repo.set_metadata(d3, title="Заключение невролога", clinic="Садко")
    repo.set_status(d3, "extracted")
    # Заключение врача Петров в Садко.
    ReportRepo(conn, uid).save([{
        "document_id": d3, "user_id": uid, "diagnosis": "Мигрень",
        "recommendations_json": '["Покой"]', "complaints_json": "[]",
        "anamnesis": None, "medications_json": "[]",
        "medications_normalized_json": None,
        "visit_date": "2026-06-10", "doctor_name": "Петров П.П.",
        "department": "Неврология",
    }])
    # Один показатель к анализу d1.
    LabRepo(conn, uid).save_results([{
        "document_id": d1, "user_id": uid, "analyte_code": None,
        "analyte_name": "Гемоглобин", "value_num": 140.0, "value_text": None,
        "unit": "г/л", "ref_low": 120.0, "ref_high": 160.0,
        "ref_operator": None, "ref_text": None, "taken_at": "2026-06-05",
        "source_table_cell": None, "value_raw": "140", "unit_raw": None,
        "taken_at_raw": None, "analyte_canonical": "Гемоглобин", "loinc": None,
        "nmu_code": None, "analyte_group": None, "match_status": "matched",
        "unit_expected": "г/л", "unit_mismatch": 0,
    }])
    return uid, (d1, d2, d3)


def test_search_filter_by_type(set_test_db):
    with get_conn() as conn:
        uid, _ = _seed_user_docs(conn)
        items, total = DocumentRepo(conn, uid).search(doc_type="analysis", limit=10)
    assert total == 2
    assert all(d["doc_type"] == "analysis" for d in items)


def test_search_filter_by_clinic(set_test_db):
    with get_conn() as conn:
        uid, _ = _seed_user_docs(conn)
        items, total = DocumentRepo(conn, uid).search(clinic="Инвитро", limit=10)
    assert total == 2
    assert all(d["clinic"] == "Инвитро" for d in items)


def test_search_filter_by_doctor(set_test_db):
    """Фильтр по врачу — через EXISTS по doctor_reports: только заключение d3."""
    with get_conn() as conn:
        uid, (d1, d2, d3) = _seed_user_docs(conn)
        items, total = DocumentRepo(conn, uid).search(doctor="Петров П.П.", limit=10)
    assert total == 1
    assert items[0]["id"] == d3


def test_search_text_query(set_test_db):
    with get_conn() as conn:
        uid, _ = _seed_user_docs(conn)
        items, total = DocumentRepo(conn, uid).search(q="биохим", limit=10)
    assert total == 1
    assert "Биохим" in items[0]["title"]


def test_search_pagination(set_test_db):
    with get_conn() as conn:
        uid, _ = _seed_user_docs(conn)
        page1, total = DocumentRepo(conn, uid).search(limit=2, offset=0)
        page2, _ = DocumentRepo(conn, uid).search(limit=2, offset=2)
    assert total == 3
    assert len(page1) == 2
    assert len(page2) == 1


def test_search_isolates_users(set_test_db):
    """Тенант-скоуп: чужие документы не видны."""
    with get_conn() as conn:
        uid_a, _ = _seed_user_docs(conn)
        uid_b = UserRepo(conn).get_or_create(99)
        items, total = DocumentRepo(conn, uid_b).search(limit=10)
    assert total == 0
    assert items == []


def test_distinct_clinics(set_test_db):
    with get_conn() as conn:
        uid, _ = _seed_user_docs(conn)
        clinics = DocumentRepo(conn, uid).distinct_clinics()
    assert set(clinics) == {"Инвитро", "Садко"}


def test_stats(set_test_db):
    with get_conn() as conn:
        uid, _ = _seed_user_docs(conn)
        s = DocumentRepo(conn, uid).stats()
    assert s["total"] == 3
    assert s["by_type"]["analysis"] == 2
    assert s["by_type"]["doctor_report"] == 1
    assert s["by_status"]["extracted"] == 3
    assert s["last"] is not None


def test_date_range(set_test_db):
    with get_conn() as conn:
        uid, _ = _seed_user_docs(conn)
        lo, hi = DocumentRepo(conn, uid).date_range()
    assert lo is not None and hi is not None


def test_distinct_analytes(set_test_db):
    with get_conn() as conn:
        uid, _ = _seed_user_docs(conn)
        names = LabRepo(conn, uid).distinct_analytes()
    assert "Гемоглобин" in names


def test_distinct_doctors(set_test_db):
    with get_conn() as conn:
        uid, _ = _seed_user_docs(conn)
        docs = ReportRepo(conn, uid).distinct_doctors()
    assert len(docs) == 1
    assert docs[0]["doctor_name"] == "Петров П.П."


def test_reports_for_period_filter_by_doctor(set_test_db):
    with get_conn() as conn:
        uid, _ = _seed_user_docs(conn)
        items, total = ReportRepo(conn, uid).for_period(doctor="Петров П.П.", limit=10)
    assert total == 1
    assert items[0]["doctor_name"] == "Петров П.П."
    # JOIN подставил клинику из documents.
    assert items[0]["clinic"] == "Садко"
