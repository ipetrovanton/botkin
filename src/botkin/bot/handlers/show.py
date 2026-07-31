"""Команда /show /last — показать последний документ."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

# Рендер деталей вынесен в нейтральный document_view (разрыв цикла импорта); реэкспортируем
# _format_* для обратной совместимости тестов/хендлеров, импортирующих их из show.
from botkin.bot.document_view import (  # noqa: F401
    _format_doctor_reports, _format_document, _format_labs, _format_ref, compose_card,
)
from botkin.bot.keyboards import card_keyboard
from botkin.bot.services import get_last_document, resolve_user

router = Router(name="show")


@router.message(Command("show", "last"))
async def cmd_show(message: Message) -> None:
    user_id = resolve_user(message.from_user.id)
    if not user_id:
        await message.answer("⚠️ Отправь /start для регистрации.")
        return
    doc = get_last_document(user_id)
    if not doc:
        await message.answer("📭 Документов пока нет.")
        return

    doc_id = doc["id"]
    await message.answer(
        compose_card(doc_id, doc),
        reply_markup=card_keyboard(doc_id, has_prev=False, has_next=False),
    )