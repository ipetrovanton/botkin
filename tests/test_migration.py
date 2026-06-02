def test_new_columns_exist(set_test_db):
    from botkin.db.connection import get_conn

    def cols(conn, table):
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    with get_conn() as conn:
        assert "raw_extraction" in cols(conn, "documents")
        assert {"drug_raw", "match_status", "reg_statuses", "reg_numbers"} <= cols(conn, "prescriptions")
        assert "medications_normalized_json" in cols(conn, "doctor_reports")
        assert {"value_raw", "unit_raw", "taken_at_raw"} <= cols(conn, "lab_results")


def test_migration_idempotent(set_test_db):
    # Повторный init_db не должен падать на уже добавленных колонках.
    from botkin.db.connection import init_db
    init_db()
    init_db()
