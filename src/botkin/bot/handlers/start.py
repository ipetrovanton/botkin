"""Команда /start — регистрация пользователя."""
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from botkin.db.connection import get_conn
from botkin.db.repos import UserRepo

router = Router(name="start")

WELCOME = (
    "👋 Привет! Я — <b>botkin</b>, ассистент для медицинских данных.\n\n"
    "Отправь мне фото, скан или PDF медицинского документа — "
    "я автоматически распознаю его и извлеку показатели.\n"
    "Используй /help для списка команд."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    with get_conn() as conn:
        UserRepo(conn).get_or_create(message.from_user.id)
    await message.answer(WELCOME)