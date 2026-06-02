"""FastAPI зависимости."""
from fastapi import Header

from botkin.db.connection import get_conn
from botkin.db.repos import UserRepo


def get_user_id(x_telegram_user_id: int = Header(..., alias="X-Telegram-User-Id")) -> int:
    """Получает user_id по telegram_user_id с авторегистрацией."""
    with get_conn() as conn:
        return UserRepo(conn).get_or_create(x_telegram_user_id)