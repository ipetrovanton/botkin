"""Аутентификация веб-кабинета: регистрация и вход по email + пароль.

Пилотный режим: без подтверждения email, без OTP. Сессия — HttpOnly cookie
на 30 дней. Telegram-бот продолжает работать через X-Telegram-User-Id.
"""
import re

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel, Field

from botkin.db.connection import get_conn
from botkin.db.repos import AuthRepo, UserRepo

router = APIRouter(prefix="/api/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PASSWORD_MIN_LENGTH = 6
_SESSION_COOKIE_NAME = "botkin_session"
_SESSION_MAX_AGE = 30 * 24 * 3600  # 30 дней в секундах


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=_PASSWORD_MIN_LENGTH, max_length=200)
    display_name: str | None = Field(None, max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=1, max_length=200)


def _validate_email(email: str) -> str:
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Некорректный email")
    return email.lower().strip()


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_SESSION_COOKIE_NAME,
        value=token,
        max_age=_SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # KeenDNS терминирует TLS на роутере; до локального — HTTP
    )


@router.post("/register")
def register(req: RegisterRequest, response: Response) -> dict:
    email = _validate_email(req.email)
    with get_conn() as conn:
        repo = UserRepo(conn)
        if repo.find_by_email(email):
            raise HTTPException(status_code=409, detail="Пользователь с таким email уже существует")
        user_id = repo.create_with_password(email, req.password, req.display_name)
        token = AuthRepo(conn).create_session(user_id)
    _set_session_cookie(response, token)
    return {"user_id": user_id, "email": email}


@router.post("/login")
def login(req: LoginRequest, response: Response) -> dict:
    email = _validate_email(req.email)
    with get_conn() as conn:
        repo = UserRepo(conn)
        user = repo.verify_credentials(email, req.password)
        if not user:
            raise HTTPException(status_code=401, detail="Неверный email или пароль")
        token = AuthRepo(conn).create_session(user["id"])
    _set_session_cookie(response, token)
    return {"user_id": user["id"], "email": email}


@router.post("/logout")
def logout(
    response: Response,
    session_token: str | None = Cookie(None, alias=_SESSION_COOKIE_NAME),
) -> dict:
    if session_token:
        with get_conn() as conn:
            AuthRepo(conn).delete_session(session_token)
    response.delete_cookie(_SESSION_COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(
    session_token: str | None = Cookie(None, alias=_SESSION_COOKIE_NAME),
) -> dict:
    if not session_token:
        raise HTTPException(status_code=401, detail="Не авторизован")
    with get_conn() as conn:
        user_id = AuthRepo(conn).get_user_id_by_token(session_token)
        if not user_id:
            raise HTTPException(status_code=401, detail="Сессия истекла")
        user = UserRepo(conn).get(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return user
