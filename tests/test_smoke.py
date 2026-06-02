"""Smoke-тесты: импорты, БД, контракты."""


def test_config_imports():
    from botkin.config import (
        VLM_MODEL, VLM_TEMPERATURE, VLM_NUM_CTX,
        VLM_MAX_TOKENS, PDF_SCALE_X, PDF_SCALE_Y, MAX_PAGES,
        SQLITE_PATH, UPLOAD_MAX_BYTES, UPLOAD_ALLOWED_EXTENSIONS,
    )
    assert isinstance(VLM_MODEL, str) and len(VLM_MODEL) > 0
    assert 0.0 <= VLM_TEMPERATURE <= 1.0
    assert VLM_NUM_CTX > 0
    assert VLM_MAX_TOKENS > 0
    assert PDF_SCALE_X > 0
    assert PDF_SCALE_Y > 0
    assert MAX_PAGES > 0
    assert len(SQLITE_PATH) > 0
    assert UPLOAD_MAX_BYTES > 0
    assert len(UPLOAD_ALLOWED_EXTENSIONS) > 0


def test_contracts_import():
    from botkin.domain.models import (
        DocType, DocStatus,
    )
    assert DocType is not None
    assert DocStatus is not None


def test_db_init(set_test_db):
    from botkin.db.connection import get_conn

    with get_conn() as conn:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

    expected = {"users", "documents", "lab_results", "prescriptions", "doctor_reports"}
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    forbidden = {"patients", "invites", "sessions", "tenants"}
    found_forbidden = forbidden & tables
    assert not found_forbidden, f"Found old tables: {found_forbidden}"


def test_auto_registration(set_test_db):
    from botkin.db.connection import get_conn
    from botkin.db.repos import UserRepo

    with get_conn() as conn:
        repo = UserRepo(conn)
        user_id = repo.get_or_create(12345)
        assert user_id > 0

        # Повторный вызов должен вернуть тот же user_id и не создавать дубликат
        user_id_2 = repo.get_or_create(12345)
        assert user_id == user_id_2

        count = conn.execute("SELECT COUNT(*) FROM users WHERE telegram_user_id = 12345").fetchone()[0]
        assert count == 1


def test_app_import():
    import botkin.api.app
    assert botkin.api.app.app is not None
    assert botkin.api.app.app.title == "botkin API"


def test_bot_imports():
    pass


def test_llm_imports():
    pass


def test_pipeline_imports():
    pass


def test_domain_models():
    from datetime import datetime
    from botkin.domain.models import LabResult, Prescription, DoctorReport, ClassifyResult, parse_ru_date

    # parse_ru_date
    dt = parse_ru_date("23 марта 2026 г.")
    assert dt == datetime(2026, 3, 23)

    # LabResult
    lab = LabResult(analyte_name="Гемоглобин", value_num=145.0, unit="г/л", ref_low=120.0, ref_high=160.0)
    assert lab.analyte_name == "Гемоглобин"
    assert lab.value_num == 145.0

    # Prescription
    rx = Prescription(drug_mnn="аторвастатин", dose="10 мг", frequency="1 раз в день")
    assert rx.drug_mnn == "аторвастатин"

    # DoctorReport
    report = DoctorReport(diagnosis="ОРВИ", doctor_name="Иванов И.И.")
    assert report.diagnosis == "ОРВИ"

    # ClassifyResult
    cr = ClassifyResult(doc_type="analysis", confidence=0.95)
    assert cr.doc_type == "analysis"