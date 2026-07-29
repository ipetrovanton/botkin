"""Репозиторий пользователей: CRUD, роли, email-аутентификация."""
from __future__ import annotations

import hashlib
import secrets
import sqlite3

from .connection import transaction


# OWASP Password Storage Cheat Sheet (2026): PBKDF2-HMAC-SHA256 — минимум 600 000 итераций.
# https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#pbkdf2
# Число итераций хранится в самом хеше (формат pbkdf2$iterations$salt$hash), поэтому
# повышение константы не требует миграции старых записей — они верифицируются по своему
# сохранённому значению.
_PBKDF2_ITERATIONS = 600_000
_SESSION_TTL_DAYS = 30


def _hash_password(password: str, salt: bytes | None = None) -> str:
    """PBKDF2-HMAC-SHA256. Возвращает 'pbkdf2$iterations$salt_hex$hash_hex'."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2":
        return False
    iterations = int(parts[1])
    salt = bytes.fromhex(parts[2])
    expected = bytes.fromhex(parts[3])
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return secrets.compare_digest(digest, expected)


class UserRepo:
    table = "users"

    ROLES = ("admin", "user")

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_or_create(self, telegram_user_id: int) -> int:
        """Возвращает user_id по telegram_user_id, создаёт при необходимости.

        Бутстрап ролей: id из ADMIN_TELEGRAM_IDS получает роль admin и при создании,
        и при повторном входе (список в конфиге мог пополниться после регистрации).
        """
        from botkin.config import ADMIN_TELEGRAM_IDS

        is_admin = telegram_user_id in ADMIN_TELEGRAM_IDS
        row = self.conn.execute(
            "SELECT id, role FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,),
        ).fetchone()
        if row:
            if is_admin and row["role"] != "admin":
                self.set_role(row["id"], "admin")
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO users(telegram_user_id, role) VALUES (?, ?)",
            (telegram_user_id, "admin" if is_admin else "user"),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_id(self, telegram_user_id: int) -> int | None:
        """user_id по telegram_user_id или None (без создания)."""
        row = self.conn.execute(
            "SELECT id FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,),
        ).fetchone()
        return row["id"] if row else None

    def get(self, user_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT id, telegram_user_id, email, role, display_name, created_at "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None

    def role_of(self, user_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return row["role"] if row else None

    def list_all(self) -> list[dict]:
        """Все пользователи со счётчиками данных — для экрана администратора."""
        rows = self.conn.execute(
            """
            SELECT u.id, u.telegram_user_id, u.email, u.role, u.display_name, u.created_at,
                   (SELECT COUNT(*) FROM documents d WHERE d.user_id = u.id) AS documents,
                   (SELECT COUNT(*) FROM lab_results l WHERE l.user_id = u.id) AS lab_results,
                   (SELECT COUNT(*) FROM doctor_reports r WHERE r.user_id = u.id) AS reports
            FROM users u ORDER BY u.id
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def create(
        self, telegram_user_id: int, display_name: str | None = None, role: str = "user",
    ) -> int:
        """Создание пользователя админом. IntegrityError по UNIQUE ловит вызывающий."""
        if role not in self.ROLES:
            raise ValueError(f"Недопустимая роль: {role}")
        cur = self.conn.execute(
            "INSERT INTO users(telegram_user_id, display_name, role) VALUES (?, ?, ?)",
            (telegram_user_id, display_name, role),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_role(self, user_id: int, role: str) -> None:
        # CHECK в мигрированных БД отсутствует — инвариант держим здесь.
        if role not in self.ROLES:
            raise ValueError(f"Недопустимая роль: {role}")
        self.conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        self.conn.commit()

    def set_display_name(self, user_id: int, display_name: str | None) -> None:
        self.conn.execute(
            "UPDATE users SET display_name = ? WHERE id = ?", (display_name, user_id)
        )
        self.conn.commit()

    def admin_count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE role = 'admin'"
        ).fetchone()["c"]

    def delete_cascade(self, user_id: int) -> list[str]:
        """Удаляет пользователя со всеми данными; возвращает source_path документов —
        файлы-исходники удаляет вызывающий (доступ к хранилищу — забота API-слоя)."""
        paths = [
            r["source_path"]
            for r in self.conn.execute(
                "SELECT source_path FROM documents WHERE user_id = ?", (user_id,)
            ).fetchall()
        ]
        with transaction(self.conn):
            for table in (
                "lab_results", "doctor_reports", "documents",
                "health_metrics", "health_activities", "health_accounts",
                "patient_profile", "patient_complaints", "patient_medications",
                "sessions",
            ):
                self.conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return paths

    def find_by_email(self, email: str) -> dict | None:
        row = self.conn.execute(
            "SELECT id, email, password_hash, role, display_name FROM users WHERE email = ?",
            (email.lower(),),
        ).fetchone()
        return dict(row) if row else None

    def create_with_password(
        self, email: str, password: str, display_name: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO users(email, password_hash, display_name, role) "
            "VALUES (?, ?, ?, 'user')",
            (email.lower(), _hash_password(password), display_name),
        )
        self.conn.commit()
        return cur.lastrowid

    def verify_credentials(self, email: str, password: str) -> dict | None:
        user = self.find_by_email(email)
        if not user or not user.get("password_hash"):
            return None
        if not _verify_password(password, user["password_hash"]):
            return None
        return user

    def link_telegram(self, user_id: int, telegram_user_id: int) -> None:
        """Привязывает telegram_user_id к существующему email-аккаунту."""
        self.conn.execute(
            "UPDATE users SET telegram_user_id = ? WHERE id = ?",
            (telegram_user_id, user_id),
        )
        self.conn.commit()


class AuthRepo:
    """Управление сессиями веб-кабинета."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_session(self, user_id: int) -> str:
        """Создаёт сессию, возвращает session_token (для cookie)."""
        from datetime import datetime, timedelta, timezone

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=_SESSION_TTL_DAYS)).isoformat()
        self.conn.execute(
            "INSERT INTO sessions(user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (user_id, token_hash, expires_at),
        )
        self.conn.commit()
        return token

    def get_user_id_by_token(self, token: str) -> int | None:
        """Возвращает user_id по session_token или None если токен невалиден/просрочен."""
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = self.conn.execute(
            "SELECT user_id, expires_at FROM sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        from datetime import datetime, timezone

        expires = datetime.fromisoformat(row["expires_at"])
        if expires < datetime.now(timezone.utc):
            self.conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            self.conn.commit()
            return None
        return row["user_id"]

    def delete_session(self, token: str) -> None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        self.conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        self.conn.commit()

    def delete_user_sessions(self, user_id: int) -> None:
        self.conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self.conn.commit()
