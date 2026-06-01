"""Отправка уведомлений пользователю через Telegram."""
import logging
import os
from typing import Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from botkin.domain.models import DOC_TYPE_LABELS

log = logging.getLogger("botkin.notifications")


async def notify_user(telegram_user_id: int, text: str) -> None:
    """Отправляет сообщение пользователю в Telegram."""
    token = os.getenv("TG_BOT_TOKEN")
    if not token:
        log.error("TG_BOT_TOKEN is not set, cannot send notification")
        return
    try:
        bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        await bot.send_message(chat_id=telegram_user_id, text=text)
        await bot.session.close()
    except Exception as e:
        log.error("Failed to send Telegram notification to %d: %s", telegram_user_id, e)


def classify_failed(document_id: int, error: str) -> str:
    return f"❌ <b>Сбой классификации</b> для документа #{document_id}.\nОшибка: {error}"


def extract_failed(document_id: int, error: str) -> str:
    return f"❌ <b>Сбой извлечения данных</b> для документа #{document_id}.\nОшибка: {error}"


def pipeline_failed(document_id: int, error: str) -> str:
    return (
        f"❌ <b>Системная ошибка обработки</b> для документа #{document_id}.\n"
        f"Пожалуйста, обратитесь к администратору или попробуйте еще раз.\n"
        f"Ошибка: {error}"
    )


def document_processed(document_id: int, doc_type: str) -> str:
    label = DOC_TYPE_LABELS.get(doc_type, "Документ 📄")
    return (
        f"✅ <b>Документ #{document_id}</b> ({label}) успешно обработан!\n"
        f"Используйте команду /show или /last, чтобы просмотреть извлеченные данные."
    )