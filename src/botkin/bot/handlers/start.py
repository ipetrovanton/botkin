"""Команда /start — регистрация пользователя."""
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from botkin.bot.services import resolve_user_or_create

router = Router(name="start")

WELCOME = (
    "👋 Привет! Я — <b>botkin</b>, ассистент для медицинских данных.\n\n"
    "Отправь мне фото, скан или PDF медицинского документа — "
    "я автоматически распознаю его и извлеку показатели.\n"
    "Используй /help для списка команд."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    resolve_user_or_create(message.from_user.id)
    await message.answer(WELCOME)