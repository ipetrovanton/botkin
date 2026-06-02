"""Фикстуры для тестов."""

import pytest


@pytest.fixture
def tmp_db_path(tmp_path):
    """Временный путь к БД."""
    return tmp_path / "test.db"


@pytest.fixture
def set_test_db(monkeypatch, tmp_db_path):
    """Подменяет SQLITE_PATH на временную БД."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_db_path))

    import importlib
    import botkin.config
    import botkin.db.connection

    importlib.reload(botkin.config)
    importlib.reload(botkin.db.connection)

    botkin.db.connection.init_db()
    return tmp_db_path