"""Команда /help."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="help")

HELP_TEXT = """
<b>📋 Доступные команды:</b>

• <b>/start</b> — Регистрация
• <b>/help</b> — Это сообщение
• <b>/show</b> или <b>/last</b> — Показать последний обработанный документ
• <b>/dynamics &lt;показатель&gt;</b> — График динамики показателя
  Пример: <code>/dynamics гемоглобин</code>

<b>📥 Как загрузить документ?</b>
Просто отправьте боту:
- <b>Фото или скан</b> (JPG, PNG, HEIC)
- <b>PDF-документ</b>

📎 <b>Лучшее качество:</b> присылайте документ <b>файлом</b> (скрепка → Файл), а не фото — сохраняется полное разрешение.

Модель qwen3-vl автоматически классифицирует документ
(Анализы 🧪, Рецепт 💊, Заключение врача 👨‍⚕️)
и извлечёт структурированные данные.
"""


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)