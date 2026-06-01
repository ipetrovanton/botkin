"""FastAPI зависимости: получение user_id из заголовка."""
from fastapi import Header
from backend.db.connection import get_conn


def get_user_id(x_telegram_user_id: int = Header(..., alias="X-Telegram-User-Id")) -> int:
    """Получает user_id по telegram_user_id. Авторегистрация при первом входе."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE telegram_user_id = ?",
            (x_telegram_user_id,),
        ).fetchone()
        if row:
            return row["id"]

        cur = conn.execute(
            "INSERT INTO users(telegram_user_id) VALUES (?)",
            (x_telegram_user_id,),
        )
        conn.commit()
        return cur.lastrowid