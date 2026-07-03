def test_new_columns_exist(set_test_db):
    from botkin.db.connection import get_conn

    def cols(conn, table):
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    with get_conn() as conn:
        assert "raw_extraction" in cols(conn, "documents")
        assert "medications_normalized_json" in cols(conn, "doctor_reports")
        assert {"value_raw", "unit_raw", "taken_at_raw"} <= cols(conn, "lab_results")


def test_documents_user_created_index_exists(set_test_db):
    """Составной индекс для ленты и навигации по соседям."""
    from botkin.db.connection import get_conn
    with get_conn() as conn:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_documents_user_created" in names


def test_prescriptions_table_dropped(set_test_db):
    """Тип prescription снят с поддержки — таблицы prescriptions быть не должно."""
    from botkin.db.connection import get_conn
    with get_conn() as conn:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "prescriptions" not in names


def test_migration_idempotent(set_test_db):
    # Повторный init_db не должен падать на уже добавленных колонках.
    from botkin.db.connection import init_db
    init_db()
    init_db()


def test_documents_has_new_columns(set_test_db):
    from botkin.db.connection import get_conn
    with get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
    assert {"title", "clinic", "delivered_at"} <= cols


def test_status_recognizing_allowed_after_migration(set_test_db):
    """На пересозданной таблице промежуточные статусы проходят CHECK."""
    from botkin.db.connection import get_conn
    from botkin.db.repos import DocumentRepo, UserRepo
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(555)
        did = DocumentRepo(conn, uid).create(source_path="/tmp/a.jpg")
        DocumentRepo(conn, uid).set_status(did, "recognizing")  # не должно бросить
        row = conn.execute("SELECT status FROM documents WHERE id=?", (did,)).fetchone()
    assert row["status"] == "recognizing"


def test_legacy_check_table_migrated_preserving_data(tmp_path, monkeypatch):
    """Старая БД со статусным CHECK без новых стадий мигрируется, данные целы."""
    import sqlite3
    import importlib
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_user_id INTEGER);"
        "INSERT INTO users(telegram_user_id) VALUES (1);"
        "CREATE TABLE documents("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, doc_type TEXT,"
        " source_path TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'received'"
        " CHECK(status IN ('received','processing','extracted','failed')),"
        " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
        "INSERT INTO documents(user_id, doc_type, source_path, status)"
        " VALUES (1,'analysis','/tmp/legacy.jpg','extracted');"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("SQLITE_PATH", str(db))
    import botkin.config
    import botkin.db.connection
    importlib.reload(botkin.config)
    importlib.reload(botkin.db.connection)
    botkin.db.connection.init_db()

    with botkin.db.connection.get_conn() as c:
        row = c.execute("SELECT source_path, status FROM documents WHERE id=1").fetchone()
        sql = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='documents'"
        ).fetchone()["sql"]
    assert row["source_path"] == "/tmp/legacy.jpg"   # данные сохранены
    assert row["status"] == "extracted"
    assert "recognizing" in sql                       # CHECK расширен


def test_legacy_prescription_doc_remapped_to_unknown(tmp_path, monkeypatch):
    """Старый документ типа prescription мигрируется в unknown, prescriptions удаляется."""
    import sqlite3
    import importlib
    db = tmp_path / "legacy_rx.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_user_id INTEGER);"
        "INSERT INTO users(telegram_user_id) VALUES (1);"
        "CREATE TABLE documents("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,"
        " doc_type TEXT CHECK(doc_type IN ('analysis','prescription','doctor_report','certificate','unknown')),"
        " source_path TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'received'"
        " CHECK(status IN ('received','processing','extracted','failed')),"
        " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
        "INSERT INTO documents(user_id, doc_type, source_path, status)"
        " VALUES (1,'prescription','/tmp/rx.jpg','extracted');"
        "CREATE TABLE prescriptions(id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " document_id INTEGER, user_id INTEGER, drug_mnn TEXT NOT NULL);"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("SQLITE_PATH", str(db))
    import botkin.config
    import botkin.db.connection
    importlib.reload(botkin.config)
    importlib.reload(botkin.db.connection)
    botkin.db.connection.init_db()

    with botkin.db.connection.get_conn() as c:
        doc_type = c.execute("SELECT doc_type FROM documents WHERE id=1").fetchone()["doc_type"]
        tables = {r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        sql = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='documents'"
        ).fetchone()["sql"]
    assert doc_type == "unknown"                  # рецепт переразмечен
    assert "prescriptions" not in tables          # таблица удалена
    assert "prescription" not in sql              # CHECK без prescription


def test_lab_results_normalization_columns(set_test_db):
    """Колонки нормализации ФСЛИ и расширенных референсов добавлены в lab_results."""
    from botkin.db.connection import get_conn
    with get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(lab_results)").fetchall()}
    assert {
        "analyte_canonical", "loinc", "nmu_code", "analyte_group",
        "match_status", "unit_expected", "unit_mismatch",
        "ref_operator", "ref_text",
    } <= cols
