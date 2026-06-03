"""Точка входа Telegram-бота."""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from botkin.bot.handlers import browse, dynamics, help, show, start, upload
from botkin.log_config import setup_logging

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

setup_logging()
log = logging.getLogger("botkin.bot")


async def main() -> None:
    token = os.getenv("TG_BOT_TOKEN")
    if not token:
        raise SystemExit("TG_BOT_TOKEN не задан в .env")

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(help.router)
    dp.include_router(upload.router)
    dp.include_router(show.router)
    dp.include_router(dynamics.router)
    dp.include_router(browse.router)  # содержит catch-all callback_query — последним

    await bot.set_my_commands([
        BotCommand(command="start", description="Регистрация"),
        BotCommand(command="help", description="Справка"),
        BotCommand(command="show", description="Последний документ"),
        BotCommand(command="list", description="Список документов"),
        BotCommand(command="period", description="Документы и показатели за период"),
        BotCommand(command="dynamics", description="График показателя"),
    ])

    log.info("Бот запущен, polling...")
    await dp.start_polling(bot)


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()