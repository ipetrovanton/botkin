"""FastAPI-зависимости."""
from fastapi import Cookie, Header, HTTPException

import botkin.config as config
from botkin.db.connection import get_conn
from botkin.db.repos import AuthRepo, UserRepo

_SESSION_COOKIE_NAME = "botkin_session"


def get_user_id(
    x_telegram_user_id: int | None = Header(None, alias="X-Telegram-User-Id"),
    session_token: str | None = Cookie(None, alias=_SESSION_COOKIE_NAME),
) -> int:
    """Получает user_id из сессии (cookie) или telegram_user_id (заголовок).

    Приоритет:
    1. Cookie botkin_session — основная аутентификация веб-кабинета.
    2. Заголовок X-Telegram-User-Id — Telegram-бот и локальная отладка.
    3. WEB_DEBUG_USER_ID — только для локального запуска без заголовка.

    WEB_DEBUG_USER_ID читается через модуль (не from-import): тесты перезагружают
    botkin.config, и значение должно подхватываться без перезагрузки deps.
    """
    if session_token:
        with get_conn() as conn:
            user_id = AuthRepo(conn).get_user_id_by_token(session_token)
        if user_id:
            return user_id

    if x_telegram_user_id is not None:
        with get_conn() as conn:
            return UserRepo(conn).get_or_create(x_telegram_user_id)

    if config.WEB_DEBUG_USER_ID > 0:
        with get_conn() as conn:
            return UserRepo(conn).get_or_create(config.WEB_DEBUG_USER_ID)

    raise HTTPException(status_code=401, detail="Требуется авторизация")


def get_telegram_user_id(
    x_telegram_user_id: int | None = Header(None, alias="X-Telegram-User-Id"),
) -> int:
    """telegram_user_id из заголовка; без заголовка — дебаг-флаг или 401.

    Сохранён для роутов, где нужен именно telegram_user_id (например, upload.py).
    """
    if x_telegram_user_id is not None:
        return x_telegram_user_id
    if config.WEB_DEBUG_USER_ID > 0:
        return config.WEB_DEBUG_USER_ID
    raise HTTPException(status_code=401, detail="X-Telegram-User-Id header required")


def require_admin(
    x_telegram_user_id: int | None = Header(None, alias="X-Telegram-User-Id"),
    session_token: str | None = Cookie(None, alias=_SESSION_COOKIE_NAME),
) -> int:
    """user_id администратора; 403 для остальных. Демо-уровень: роль читается из БД,
    подлинность заголовка не проверяется (осознанное решение из интервью)."""
    user_id = get_user_id(x_telegram_user_id, session_token)
    with get_conn() as conn:
        if UserRepo(conn).role_of(user_id) != "admin":
            raise HTTPException(status_code=403, detail="Требуется роль администратора")
    return user_id
