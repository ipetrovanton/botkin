"""Команда /start — регистрация пользователя."""
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from backend.db.connection import get_conn

router = Router(name="start")

WELCOME = (
    "👋 Привет! Я — <b>botkin</b>, ассистент для медицинских данных.\n\n"
    "Отправь мне фото, скан или PDF медицинского документа — "
    "я автоматически распознаю его и извлеку показатели.\n"
    "Используй /help для списка команд."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    tg_user_id = message.from_user.id
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE telegram_user_id = ?", (tg_user_id,)
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users(telegram_user_id) VALUES (?)", (tg_user_id,)
            )
            conn.commit()
    await message.answer(WELCOME)