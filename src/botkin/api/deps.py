"""FastAPI зависимости."""
from fastapi import Header, HTTPException

import botkin.config as config
from botkin.db.connection import get_conn
from botkin.db.repos import UserRepo


def get_telegram_user_id(
    x_telegram_user_id: int | None = Header(None, alias="X-Telegram-User-Id"),
) -> int:
    """telegram_user_id из заголовка; без заголовка — дебаг-флаг или 401.

    WEB_DEBUG_USER_ID читается через модуль (не from-import): тесты перезагружают
    botkin.config, и значение должно подхватываться без перезагрузки deps.
    """
    if x_telegram_user_id is not None:
        return x_telegram_user_id
    if config.WEB_DEBUG_USER_ID > 0:
        return config.WEB_DEBUG_USER_ID
    raise HTTPException(status_code=401, detail="X-Telegram-User-Id header required")


def get_user_id(x_telegram_user_id: int | None = Header(None, alias="X-Telegram-User-Id")) -> int:
    """Получает user_id по telegram_user_id с авторегистрацией."""
    tg_id = get_telegram_user_id(x_telegram_user_id)
    with get_conn() as conn:
        return UserRepo(conn).get_or_create(tg_id)


def require_admin(
    x_telegram_user_id: int | None = Header(None, alias="X-Telegram-User-Id"),
) -> int:
    """user_id администратора; 403 для остальных. Демо-уровень: роль читается из БД,
    подлинность заголовка не проверяется (осознанное решение из интервью)."""
    user_id = get_user_id(x_telegram_user_id)
    with get_conn() as conn:
        if UserRepo(conn).role_of(user_id) != "admin":
            raise HTTPException(status_code=403, detail="Требуется роль администратора")
    return user_id
