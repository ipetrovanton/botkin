"""Smoke-тесты: проверка импортов и инициализации БД."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_config_imports():
    """Проверка импорта конфигурации."""
    from backend.config import (
        VLM_MODEL, VLM_TEMPERATURE, VLM_NUM_CTX, VLM_NUM_PREDICT,
        VLM_MAX_TOKENS, PDF_SCALE_X, PDF_SCALE_Y, MAX_PAGES,
        SQLITE_PATH, UPLOAD_MAX_BYTES, UPLOAD_ALLOWED_EXTENSIONS,
    )
    assert isinstance(VLM_MODEL, str) and len(VLM_MODEL) > 0
    assert 0.0 <= VLM_TEMPERATURE <= 1.0
    assert VLM_NUM_CTX > 0
    assert VLM_NUM_PREDICT > 0
    assert VLM_MAX_TOKENS > 0
    assert PDF_SCALE_X > 0
    assert PDF_SCALE_Y > 0
    assert MAX_PAGES > 0
    assert len(SQLITE_PATH) > 0
    assert UPLOAD_MAX_BYTES > 0
    assert len(UPLOAD_ALLOWED_EXTENSIONS) > 0


def test_contracts_import():
    """Проверка импорта контрактов."""
    from backend.contracts import (
        LabResult, Prescription, DoctorReport,
        ClassifyResult, UploadResponse,
        DocType, DocStatus,
    )
    assert DocType is not None
    assert DocStatus is not None
    assert LabResult is not None
    assert Prescription is not None
    assert DoctorReport is not None
    assert ClassifyResult is not None
    assert UploadResponse is not None


def test_db_init(tmp_path):
    """Проверка инициализации БД."""
    from backend.db.connection import get_conn

    db_path = tmp_path / "test.db"
    os.environ["SQLITE_PATH"] = str(db_path)

    # Перезагружаем модуль с новым путём
    import importlib
    import backend.config
    import backend.db.connection

    importlib.reload(backend.config)
    importlib.reload(backend.db.connection)

    backend.db.connection.init_db()

    with get_conn() as conn:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    expected = {"users", "documents", "lab_results", "prescriptions", "doctor_reports"}
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    # Проверяем, что нет старых таблиц
    forbidden = {"patients", "invites", "sessions", "tenants"}
    found_forbidden = forbidden & tables
    assert not found_forbidden, f"Found old tables: {found_forbidden}"


def test_auto_registration():
    """Проверка авторегистрации пользователя."""
    from backend.db.connection import get_conn

    with get_conn() as conn:
        # Первый запрос — создаёт пользователя
        cur = conn.execute(
            "INSERT INTO users(telegram_user_id) VALUES (12345) "
            "ON CONFLICT(telegram_user_id) DO NOTHING"
        )
        if cur.rowcount > 0:
            conn.commit()

        user = conn.execute(
            "SELECT id FROM users WHERE telegram_user_id = 12345"
        ).fetchone()
        assert user is not None
        assert user["id"] > 0

        # Повторный — не дублирует
        conn.execute(
            "INSERT INTO users(telegram_user_id) VALUES (12345) "
            "ON CONFLICT(telegram_user_id) DO NOTHING"
        )
        conn.commit()

        count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE telegram_user_id = 12345"
        ).fetchone()[0]
        assert count == 1


def test_app_import():
    """Проверка импорта приложения."""
    import backend.app
    assert backend.app.app is not None
    assert backend.app.app.title == "botkin API"


def test_bot_imports():
    """Проверка импорта модулей бота."""
    import bot_and_rag.bot.main
    import bot_and_rag.viz.plots
    import bot_and_rag.bot.handlers.start
    import bot_and_rag.bot.handlers.help
    import bot_and_rag.bot.handlers.upload
    import bot_and_rag.bot.handlers.show
    import bot_and_rag.bot.handlers.dynamics