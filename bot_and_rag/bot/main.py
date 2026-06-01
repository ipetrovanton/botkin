"""Точка входа Telegram-бота."""
import asyncio
import logging
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .handlers import start, help, upload, show, dynamics

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("botkin.bot")


async def main() -> None:
    token = os.getenv("TG_BOT_TOKEN")
    if not token:
        raise SystemExit("❌ TG_BOT_TOKEN не задан в .env")

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(help.router)
    dp.include_router(upload.router)
    dp.include_router(show.router)
    dp.include_router(dynamics.router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Регистрация"),
        BotCommand(command="help", description="Список команд"),
        BotCommand(command="show", description="Показать последний документ"),
        BotCommand(command="dynamics", description="График динамики показателя"),
    ])

    log.info("✅ Bot started, polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped")